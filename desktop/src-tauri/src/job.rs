//! Windows Job Object: the sidecar (and anything that does NOT explicitly escape) dies when
//! the app does.
//!
//! Windows does not terminate child processes when their parent exits or crashes, so a hard-killed
//! app would leave the Python sidecar running. The job is assigned to the *sidecar process
//! specifically* - never to this app process itself - with KILL_ON_JOB_CLOSE; the job handle is
//! held open for as long as this app process runs, and the OS closes it (killing the sidecar's
//! part of the tree) however this process ends, crash included.
//!
//! That specificity matters: assigning the job to *this* process instead would make every future
//! descendant of it a job member by inheritance - including a child this process has not spawned
//! yet. The auto-updater is exactly that case: on Windows it launches the downloaded installer via
//! `ShellExecuteW` and then calls `std::process::exit(0)` within the same instant, bypassing
//! Tauri's own exit event entirely. A job assigned to this process would inherit onto that
//! installer, and `KILL_ON_JOB_CLOSE` would kill it the moment this process's own handles close -
//! before the installer could do anything. Assigning the job to the sidecar alone keeps that
//! blast radius to exactly the one process it is meant to contain.
//!
//! The monitoring worker (and the FFmpeg it runs) must NOT die with this job either: Phase2.md
//! section 10 requires monitoring to survive the UI closing or crashing ("Close Main Window:
//! Monitoring continues if an active session exists"). `agent.launcher.spawn_worker` already
//! launches the worker with `CREATE_BREAKAWAY_FROM_JOB` for exactly this reason - but that
//! request only succeeds if the job itself opts in with `JOB_OBJECT_LIMIT_BREAKAWAY_OK`. Without
//! it, Windows denies the breakaway, `spawn_worker` silently falls back to no breakaway, and the
//! worker (and every FFmpeg it starts) ends up trapped in the sidecar's job after all. Lifecycle
//! ownership is therefore explicit and split in two: this job owns the sidecar's forced-cleanup
//! path; the worker opts itself out of it and is owned by nothing but its own detached process
//! group, cleaned up only by `agent stop`/recovery (see orchestrator.py's `_reap_orphan_capture`)
//! or an explicit Stop Monitoring / Quit-and-stop action.

#[cfg(windows)]
pub struct SidecarJob(Option<isize>);

#[cfg(windows)]
impl SidecarJob {
    /// A job-less placeholder for when `create()` failed: `assign_process` becomes a no-op
    /// rather than something every caller needs to special-case.
    pub fn disabled() -> Self {
        Self(None)
    }

    /// Creates the job with KILL_ON_JOB_CLOSE + BREAKAWAY_OK, assigned to no process yet.
    pub fn create() -> Result<Self, String> {
        use std::mem::{size_of, zeroed};
        use windows_sys::Win32::Foundation::{CloseHandle, GetLastError};
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        }; // fmt: skip

        // SAFETY: plain Win32 calls with correctly sized, zero-initialised structs.
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err(format!("CreateJobObjectW failed: {}", GetLastError()));
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            info.BasicLimitInformation.LimitFlags =
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
            let ok = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                let err = GetLastError();
                CloseHandle(job);
                return Err(format!("SetInformationJobObject failed: {err}"));
            }
            Ok(Self(Some(job as isize)))
        }
    }

    /// Puts one process (and, unless it breaks away, its future children) under this job.
    /// Intended for the sidecar process specifically - see the module docs for why. A no-op
    /// if the job itself failed to create (`disabled()`); containment is best effort.
    pub fn assign_process(&self, handle: std::os::windows::io::RawHandle) -> Result<(), String> {
        use windows_sys::Win32::Foundation::GetLastError;
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;

        let Some(job) = self.0 else { return Ok(()) };
        // SAFETY: `handle` is a live process handle owned by the caller (a std::process::Child
        // it still holds); AssignProcessToJobObject does not take ownership of it.
        unsafe {
            if AssignProcessToJobObject(job as _, handle as _) == 0 {
                return Err(format!(
                    "AssignProcessToJobObject failed: {}",
                    GetLastError()
                ));
            }
        }
        Ok(())
    }
}

// Deliberately no `Drop`/`CloseHandle`: the job must stay alive exactly as long as this app
// process does, and process exit closes every handle the process owns, crash included.
#[cfg(windows)]
unsafe impl Send for SidecarJob {}
#[cfg(windows)]
unsafe impl Sync for SidecarJob {}

#[cfg(not(windows))]
pub struct SidecarJob;

#[cfg(not(windows))]
impl SidecarJob {
    pub fn disabled() -> Self {
        Self
    }

    pub fn create() -> Result<Self, String> {
        Ok(Self)
    }

    pub fn assign_process(&self, _handle: ()) -> Result<(), String> {
        Ok(())
    }
}
