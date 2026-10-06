//! Windows Job Object: every process the desktop spawns dies when the desktop does.
//!
//! Windows does not terminate child processes when their parent exits or crashes, so a hard-killed
//! app would leave the Python sidecar (and later FFmpeg) running. We put the app itself in a job
//! with KILL_ON_JOB_CLOSE; children inherit membership, and the OS closes the job handle -
//! killing the whole tree - however the app ends.

#[cfg(windows)]
pub fn contain_process_tree() -> Result<(), String> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::GetCurrentProcess;

    // SAFETY: plain Win32 calls with correctly sized, zero-initialised structs; the job handle
    // is deliberately never closed so it lives exactly as long as this process.
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(format!("CreateJobObjectW failed: {}", GetLastError()));
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
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
        if AssignProcessToJobObject(job, GetCurrentProcess()) == 0 {
            let err = GetLastError();
            CloseHandle(job);
            return Err(format!("AssignProcessToJobObject failed: {err}"));
        }
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn contain_process_tree() -> Result<(), String> {
    Ok(())
}
