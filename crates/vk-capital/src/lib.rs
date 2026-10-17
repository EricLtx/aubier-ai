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
