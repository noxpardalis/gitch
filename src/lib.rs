use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;

use std::fmt::Write;
use std::path::PathBuf;
use std::str::FromStr;

#[pyclass]
#[derive(Clone)]
pub struct Signature {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub email: String,
}

#[pyclass]
pub struct Commit {
    inner: gix::ObjectDetached,

    #[pyo3(get)]
    pub id: String,
    #[pyo3(get)]
    pub summary: String,
    #[pyo3(get)]
    pub body: Option<String>,
    pub time: jiff::civil::DateTime,
    #[pyo3(get)]
    pub author: Signature,
    #[pyo3(get)]
    pub committer: Signature,
    #[pyo3(get)]
    pub trailers: std::collections::HashMap<String, std::collections::HashSet<String>>,
}

#[pymethods]
impl Commit {
    #[getter]
    fn time(&self) -> String {
        self.time.to_string()
    }
}

impl<'r> TryFrom<gix::revision::walk::Info<'r>> for Commit {
    type Error = ();

    fn try_from(info: gix::revision::walk::Info<'r>) -> Result<Self, Self::Error> {
        let commit_object = info.object().unwrap();
        let commit = commit_object.decode().unwrap();
        // Get the commit ID.
        let id = info.id().to_string();

        // Get the commit time.
        let seconds = info.commit_time();
        let time = jiff::Timestamp::from_second(seconds)
            .unwrap()
            .to_zoned(jiff::tz::TimeZone::system())
            .datetime();

        // Get the commit author.
        let author = commit.author();
        let author = Signature {
            name: author.name.to_string(),
            email: author.email.to_string(),
        };

        // Get the commit committer.
        let committer = commit.committer();
        let committer = Signature {
            name: committer.name.to_string(),
            email: committer.email.to_string(),
        };

        // Get commit summary.
        let summary = commit.message_summary().to_string();

        // Get commit trailers.
        let trailers =
            commit
                .message_trailers()
                .fold(std::collections::HashMap::new(), |mut acc, trailer| {
                    let token = trailer.token.to_string();
                    let value = trailer.value.to_string();
                    let trailer: &mut std::collections::HashSet<_> = acc.entry(token).or_default();
                    trailer.insert(value);
                    acc
                });

        // Get commit body.
        let body = match commit.message().body {
            Some(body) => {
                let mut message: Vec<u8> = b"\n\n".to_vec();
                message.extend_from_slice(body);
                let body = gix::diff::object::commit::message::BodyRef::from_bytes(&message[..])
                    .without_trailer();
                if body.is_empty() {
                    None
                } else {
                    Some(gix::diff::object::bstr::BStr::new(body.trim_ascii()).to_string())
                }
            }
            None => None,
        };

        Ok(Commit {
            inner: commit_object.detached(),
            id,
            summary,
            body,
            time,
            author,
            committer,
            trailers,
        })
    }
}

#[pyclass]
#[derive(Clone, Copy)]
pub enum Algorithm {
    Histogram,
    Myers,
    MyersMinimal,
}

impl From<Algorithm> for gix::diff::blob::Algorithm {
    fn from(algorithm: Algorithm) -> Self {
        match algorithm {
            Algorithm::Histogram => gix::diff::blob::Algorithm::Histogram,
            Algorithm::Myers => gix::diff::blob::Algorithm::Myers,
            Algorithm::MyersMinimal => gix::diff::blob::Algorithm::MyersMinimal,
        }
    }
}

#[pyclass]
pub struct Repository {
    inner: gix::ThreadSafeRepository,
}

impl Repository {
    fn unified_diff_files(
        resource_cache: &mut gix::diff::blob::Platform,
        objects: &gix::OdbHandle,
        algorithm: gix::diff::blob::Algorithm,
        older_id: &gix::oid,
        older_location: &gix::diff::object::bstr::BStr,
        newer_id: &gix::oid,
        newer_location: &gix::diff::object::bstr::BStr,
    ) -> Result<String, ()> {
        resource_cache
            .set_resource(
                older_id.into(),
                gix::object::tree::EntryKind::Blob,
                older_location.as_ref(),
                gix::diff::blob::ResourceKind::OldOrSource,
                objects,
            )
            .unwrap();
        resource_cache
            .set_resource(
                newer_id.into(),
                gix::object::tree::EntryKind::Blob,
                newer_location.as_ref(),
                gix::diff::blob::ResourceKind::NewOrDestination,
                objects,
            )
            .unwrap();
        let outcome = resource_cache.prepare_diff().unwrap();

        let interner = gix::diff::blob::intern::InternedInput::new(
            gix::diff::blob::sources::byte_lines(outcome.old.data.as_slice().unwrap_or_default()),
            gix::diff::blob::sources::byte_lines(outcome.new.data.as_slice().unwrap_or_default()),
        );

        let unified_diff = gix::diff::blob::UnifiedDiff::new(
            &interner,
            String::new(),
            gix::diff::blob::unified_diff::NewlineSeparator::AfterHeaderAndLine("\n"),
            gix::diff::blob::unified_diff::ContextSize::symmetrical(3),
        );

        Ok(gix::diff::blob::diff(algorithm, &interner, unified_diff).unwrap())
    }

    fn diff_with_parent(
        commit: &gix::Commit<'_>,
        algorithm: gix::diff::blob::Algorithm,
    ) -> Result<Option<String>, ()> {
        let tree = commit.tree().unwrap();
        let parent_tree = if let Some(parent_id) = commit.parent_ids().next() {
            parent_id.object().unwrap().peel_to_tree().unwrap()
        } else {
            tree.repo.empty_tree()
        };

        let deltas = commit
            .repo
            .diff_tree_to_tree(Some(&parent_tree), Some(&tree), None)
            .unwrap();

        let mut diff = String::new();
        let mut resource_cache = commit
            .repo
            .diff_resource_cache(
                gix::diff::blob::pipeline::Mode::ToGitUnlessBinaryToTextIsPresent,
                Default::default(),
            )
            .unwrap();
        let objects = &commit.repo.objects;

        for delta in deltas {
            let (older_location, newer_location, older_id, newer_id) = match &delta {
                gix::object::tree::diff::ChangeDetached::Addition {
                    location,
                    entry_mode,
                    id,
                    ..
                } => {
                    // Skip anything that's not blob-diffable.
                    // This includes the addition of new directories that git
                    // will not normally show.
                    if !entry_mode.is_blob() {
                        continue;
                    }
                    // older is nothing
                    // newer is everything
                    let previous_id = gix::index::hash::Kind::Sha1.null();
                    let backing = &mut [0; 6];
                    writeln!(diff, "diff --git a/{location} b/{location}").unwrap();
                    writeln!(diff, "new file mode {}", entry_mode.as_bytes(backing)).unwrap();
                    writeln!(
                        diff,
                        "index {}..{}",
                        &previous_id.to_string()[0..7],
                        &id.to_string()[0..7],
                    )
                    .unwrap();
                    writeln!(diff, "--- /dev/null").unwrap();
                    writeln!(diff, "+++ b/{location}").unwrap();
                    (
                        location.as_ref(),
                        location.as_ref(),
                        &gix::index::hash::Kind::Sha1.null(),
                        id,
                    )
                }
                gix::object::tree::diff::ChangeDetached::Deletion {
                    location,
                    entry_mode,
                    id,
                    ..
                } => {
                    // Skip anything that's not blob-diffable.
                    // This includes the addition of new directories that git
                    // will not normally show.
                    if !entry_mode.is_blob() {
                        continue;
                    }
                    // newer is nothing
                    // older is everything
                    let newer_id = gix::index::hash::Kind::Sha1.null();
                    let backing = &mut [0; 6];
                    writeln!(diff, "diff --git a/{location} b/{location}").unwrap();
                    writeln!(diff, "deleted file mode {}", entry_mode.as_bytes(backing)).unwrap();

                    writeln!(
                        diff,
                        "index {}..{}",
                        &id.to_string()[0..7],
                        &newer_id.to_string()[0..7],
                    )
                    .unwrap();
                    writeln!(diff, "--- a/{location}").unwrap();
                    writeln!(diff, "+++ /dev/null").unwrap();
                    (
                        location.as_ref(),
                        location.as_ref(),
                        id,
                        &gix::index::hash::Kind::Sha1.null(),
                    )
                }
                gix::object::tree::diff::ChangeDetached::Modification {
                    location,
                    entry_mode,
                    previous_id,
                    id,
                    ..
                } => {
                    // Skip anything that's not blob-diffable.
                    // This includes the addition of new directories that git
                    // will not normally show.
                    if !entry_mode.is_blob() {
                        continue;
                    }

                    writeln!(diff, "diff --git a/{location} b/{location}").unwrap();
                    let backing = &mut [0; 6];
                    writeln!(
                        diff,
                        "index {}..{} {}",
                        &previous_id.to_string()[0..7],
                        &id.to_string()[0..7],
                        entry_mode.as_bytes(backing)
                    )
                    .unwrap();
                    writeln!(diff, "--- a/{location}").unwrap();
                    writeln!(diff, "+++ b/{location}").unwrap();

                    (location.as_ref(), location.as_ref(), previous_id, id)
                }
                gix::object::tree::diff::ChangeDetached::Rewrite {
                    source_location,
                    location,
                    source_entry_mode,
                    entry_mode,
                    source_id,
                    id,
                    ..
                } => {
                    if !(source_entry_mode.is_blob() && entry_mode.is_blob()) {
                        continue;
                    }

                    writeln!(diff, "diff --git a/{source_location} b/{location}").unwrap();
                    if id == source_id {
                        // This is a perfect copy.
                        let backing = &mut [0; 6];
                        writeln!(diff, "old mode {}", source_entry_mode.as_bytes(backing)).unwrap();
                        writeln!(diff, "new mode {}", entry_mode.as_bytes(backing)).unwrap();
                        writeln!(diff, "similarity index 100%").unwrap();
                        writeln!(diff, "rename from {source_location}").unwrap();
                        writeln!(diff, "rename to {location}").unwrap();
                        continue;
                    } else {
                        // TODO(noxpardalis): what to do if the entry modes are different?
                        assert_eq!(source_entry_mode, entry_mode);

                        let backing = &mut [0; 6];
                        writeln!(
                            diff,
                            "index {}..{} {}",
                            &source_id.to_string()[0..7],
                            &id.to_string()[0..7],
                            entry_mode.as_bytes(backing)
                        )
                        .unwrap();
                        writeln!(diff, "--- a/{source_location}").unwrap();
                        writeln!(diff, "+++ b/{location}").unwrap();
                    }
                    (source_location.as_ref(), location.as_ref(), source_id, id)
                }
            };

            writeln!(
                diff,
                "{}",
                Self::unified_diff_files(
                    &mut resource_cache,
                    objects,
                    algorithm,
                    older_id,
                    older_location,
                    newer_id,
                    newer_location,
                )
                .unwrap()
                .trim()
            )
            .unwrap();
        }
        if diff.is_empty() {
            Ok(None)
        } else {
            Ok(Some(diff))
        }
    }
}

trait IntoPyResult {
    type T;
    type Err;
    fn into_py_result(self) -> PyResult<Self::T>;
}

impl<T, E: std::error::Error> IntoPyResult for Result<T, E> {
    type T = T;
    type Err = PyOSError;
    fn into_py_result(self) -> PyResult<T> {
        self.map_err(|e| PyErr::new::<<Self as IntoPyResult>::Err, _>(format!("{e}")))
    }
}

fn try_parse_start_timestamp(str: &str) -> Result<jiff::Timestamp, jiff::Error> {
    // Try timestamp
    if let Ok(timestamp) = jiff::Timestamp::from_str(str) {
        Ok(timestamp)
    } else {
        // Try date time
        if let Ok(dt) = jiff::civil::DateTime::from_str(str) {
            Ok(dt.to_zoned(jiff::tz::TimeZone::system())?.timestamp())
        } else {
            // Try date set to start of day.
            jiff::civil::Date::from_str(str)
                .map(|d| d.to_zoned(jiff::tz::TimeZone::system()))?
                .map(|d| d.start_of_day())?
                .map(|d| d.timestamp())
        }
    }
}

fn try_parse_end_timestamp(str: &str) -> Result<jiff::Timestamp, jiff::Error> {
    // Try timestamp
    if let Ok(timestamp) = jiff::Timestamp::from_str(str) {
        Ok(timestamp)
    } else {
        // Try date time
        if let Ok(dt) = jiff::civil::DateTime::from_str(str) {
            Ok(dt.to_zoned(jiff::tz::TimeZone::system())?.timestamp())
        } else {
            // Try date set to end of day.
            jiff::civil::Date::from_str(str)
                .map(|d| d.to_zoned(jiff::tz::TimeZone::system()))?
                .map(|d| d.end_of_day())?
                .map(|d| d.timestamp())
        }
    }
}

#[pymethods]
impl Repository {
    #[new]
    pub fn new(repository: PathBuf) -> PyResult<Self> {
        let inner = gix::discover(&repository).into_py_result()?.into_sync();
        Ok(Self { inner })
    }

    #[getter]
    fn root(&self) -> PyResult<PathBuf> {
        self.inner
            .path()
            .parent()
            .expect("could not get parent of .git directory")
            .canonicalize()
            .into_py_result()
    }

    pub fn diff(&self, commit: &Commit, algorithm: Algorithm) -> Option<String> {
        let repository = self.inner.to_thread_local();
        let commit = commit.inner.clone().attach(&repository);
        let commit = commit.into_commit();
        Repository::diff_with_parent(&commit, algorithm.into()).unwrap()
    }

    #[pyo3(
        signature=(
            commit_start_cutoff=None,
            commit_end_cutoff=None,
            cutoff_start_timestamp=None,
            cutoff_end_timestamp=None
        ))]
    pub fn commits(
        &self,
        commit_start_cutoff: Option<&str>,
        commit_end_cutoff: Option<&str>,
        cutoff_start_timestamp: Option<&str>,
        cutoff_end_timestamp: Option<&str>,
    ) -> PyResult<Vec<Commit>> {
        let commit_start_cutoff =
            commit_start_cutoff.map(|cutoff| gix::ObjectId::from_str(cutoff).unwrap());
        let commit_end_cutoff =
            commit_end_cutoff.map(|cutoff| gix::ObjectId::from_str(cutoff).unwrap());
        let cutoff_start_timestamp = cutoff_start_timestamp
            .map(try_parse_start_timestamp)
            .transpose()
            .into_py_result()?
            .map(|timestamp| {
                timestamp
                    .duration_since(jiff::Timestamp::UNIX_EPOCH)
                    .as_secs()
            });
        let cutoff_end_timestamp = cutoff_end_timestamp
            .map(try_parse_end_timestamp)
            .transpose()
            .into_py_result()?
            .map(|timestamp| {
                timestamp
                    .duration_since(jiff::Timestamp::UNIX_EPOCH)
                    .as_secs()
            });

        let repository = self.inner.to_thread_local();
        let target = repository
            .head()
            .unwrap()
            .peel_to_commit_in_place()
            .unwrap();
        let commits = target
            .ancestors()
            .sorting(if let Some(cutoff) = cutoff_start_timestamp {
                gix::revision::walk::Sorting::ByCommitTimeCutoff {
                    order: gix::traverse::commit::simple::CommitTimeOrder::NewestFirst,
                    seconds: cutoff,
                }
            } else {
                gix::revision::walk::Sorting::ByCommitTime(
                    gix::traverse::commit::simple::CommitTimeOrder::NewestFirst,
                )
            })
            .all()
            .unwrap()
            .flatten()
            .skip_while(move |info| {
                if let Some(id_cutoff) = commit_end_cutoff {
                    id_cutoff != info.id
                } else {
                    false
                }
            })
            .skip_while(move |info| {
                if let (Some(commit_time), Some(cutoff)) = (info.commit_time, cutoff_end_timestamp)
                {
                    commit_time > cutoff
                } else {
                    false
                }
            })
            .scan(false, move |cutoff_seen, info| {
                if *cutoff_seen {
                    None
                } else if Some(info.id) == commit_start_cutoff {
                    *cutoff_seen = true;
                    Some(info)
                } else {
                    Some(info)
                }
            })
            .map(move |info| Commit::try_from(info).unwrap())
            .collect::<Vec<_>>();

        Ok(commits)
    }

    pub fn first_commit(&self) -> PyResult<Commit> {
        let repository = self.inner.to_thread_local();
        let target = repository
            .head()
            .unwrap()
            .peel_to_commit_in_place()
            .unwrap();

        let commit = target
            .ancestors()
            .sorting(gix::revision::walk::Sorting::ByCommitTime(
                gix::traverse::commit::simple::CommitTimeOrder::NewestFirst,
            ))
            .all()
            .unwrap()
            .last()
            .unwrap()
            .unwrap()
            .try_into()
            .unwrap();

        Ok(commit)
    }
}

#[pymodule]
#[pyo3(name = "gitch_core")]
fn gitch_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Repository>()?;
    m.add_class::<Commit>()?;
    m.add_class::<Signature>()?;
    m.add_class::<Algorithm>()?;
    Ok(())
}
