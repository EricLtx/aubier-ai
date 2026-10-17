//! The capital verbs: `vk capital key init|show`, the publisher key that
//! signs this account's driver packages, and `vk driver pack`, which makes
//! one (packaging spec §4.4–4.5). Each is one syscall; the two that make or
//! sign something are human acts, carrying a presence proof signed with this
//! node's device key.
use crate::{plain_path, presence, render, show, Cli};
use anyhow::{Context, Result};
use clap::{Args, Subcommand};
use serde_json::json;
use std::path::PathBuf;
use vk_ipc::client::Client;

#[derive(Subcommand)]
pub enum CapitalCmd {
    /// The publisher key: the Ed25519 key that signs this account's driver
    /// packages, in this account's keyring (`vk`/`publisher`).
    Key {
        #[command(subcommand)]
        what: KeyCmd,
    },
}

#[derive(Subcommand)]
pub enum KeyCmd {
    /// Make the publisher key, and pin its public half on this node, so
    /// this node installs what it packs. Once: a second run shows the same
    /// key. Human: signed with this node's device key. Prints the public key
    /// to hand to the nodes that will install your packages.
    Init,
    /// The publisher key's id and public half, and whether this node pins
    /// it. Never makes one.
    Show,
}

/// `vk driver pack`'s arguments.
#[derive(Args)]
pub struct PackArgs {
    /// The development driver's manifest (JSON), next to its files.
    #[arg(long, value_name = "FILE")]
    manifest: PathBuf,
    /// The package's version, MAJOR.MINOR.PATCH (`1.0.0`): it installs at
    /// `<install root>/<name>/<version>` and mounts as `<name>@<version>`.
    #[arg(long)]
    version: String,
    /// Where the package is written: a new or empty directory.
    #[arg(long, value_name = "DIR")]
    out: PathBuf,
    /// Where the pinned CPython release is downloaded to, and found again
    /// (default: under the daemon's state directory).
    #[arg(long, value_name = "DIR")]
    python_cache: Option<PathBuf>,
}

pub async fn capital(cli: &Cli, c: &Client, what: &CapitalCmd) -> Result<()> {
    match what {
        CapitalCmd::Key { what: KeyCmd::Init } => {
            let proof = presence(c).await?;
            show(
                cli,
                c.call("capital.key_init", json!({}), Some(proof)).await?,
                render::publisher_key,
            )
        }
        CapitalCmd::Key { what: KeyCmd::Show } => show(
            cli,
            c.call("capital.key_show", json!({}), None).await?,
            render::publisher_key,
        ),
    }
}

/// `vk driver pack`: the paths made absolute here, where they mean what the
/// person typed — the daemon's working directory is its own — the manifest
/// resolved (it must exist), then the request with a presence proof. The
/// daemon refuses before it writes anything what it can: the version, an
/// `--out` that is not empty, a node with no publisher key.
pub async fn pack(cli: &Cli, c: &Client, args: &PackArgs) -> Result<()> {
    let manifest = std::fs::canonicalize(&args.manifest)
        .with_context(|| format!("resolve the driver manifest {}", args.manifest.display()))?;
    let absolute = |p: &PathBuf| {
        std::path::absolute(p).with_context(|| format!("make {} absolute", p.display()))
    };
    let out = absolute(&args.out)?;
    let python_cache = args.python_cache.as_ref().map(absolute).transpose()?;
    let proof = presence(c).await?;
    show(
        cli,
        c.call(
            "driver.pack",
            json!({
                "manifest": plain_path(&manifest),
                "version": args.version,
                "out": plain_path(&out),
                "python_cache": python_cache.as_deref().map(plain_path),
            }),
            Some(proof),
        )
        .await?,
        render::packed,
    )
}
