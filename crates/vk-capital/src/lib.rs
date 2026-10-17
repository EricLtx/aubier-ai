//! The capital package, first component: a driver that leaves the
//! development tree as a signed OCI image layout and comes back as an
//! installed, fully hashed tree. Depends on `vk-contracts` only.
//!
//! OS analogy: a package is a firmware image — everything the supervisor
//! will hash and run, nothing that depends on the board.

pub mod error;
pub mod fingerprint;
pub mod layout;
pub mod materialise;
pub mod python;
pub mod signature;
pub mod wheels;

pub use error::CapitalError;

/// `<pid>-<nanos>-<n>`: a file-name suffix that no other call, in this
/// process or in another one running beside it, returns.
pub(crate) fn unique_suffix() -> String {
    use std::sync::atomic::{AtomicU32, Ordering};
    static NEXT: AtomicU32 = AtomicU32::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    format!(
        "{}-{nanos}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    )
}
