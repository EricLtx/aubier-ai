#[derive(Debug, thiserror::Error)]
pub enum CapitalError {
    #[error("{0}")]
    Refused(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, CapitalError>;

/// A refusal in the kernel's voice: what was wrong, then `remedy: …`.
pub fn refuse<T>(what: impl Into<String>, remedy: &str) -> Result<T> {
    Err(CapitalError::Refused(format!(
        "{}; remedy: {remedy}",
        what.into()
    )))
}
