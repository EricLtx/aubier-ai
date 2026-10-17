//! The capital verbs: `vk capital key init|show`, the publisher key that
//! signs this account's driver packages, and `vk driver pack`, which makes
//! one (packaging spec §4.4–4.5). Each is one syscall; the two that make or
//! sign something are human acts, carrying a presence proof signed with this
//! node's device key.
use crate::{plain_path, presence, render, show, Cli};
use anyhow::{anyhow, Context, Result};
use clap::{Args, Subcommand};
use serde_json::json;
use std::path::{Path, PathBuf};
use vk_ipc::client::Client;

#[derive(Subcommand)]
pub enum CapitalCmd {
    /// The publisher key: the Ed25519 key that signs this account's driver
    /// packages, in this account's keyring (`vk`/`publisher`).
    Key {
        #[command(subcommand)]
        what: KeyCmd,
    },
    /// The pinned CPython release, for CI (hidden: not a node's business).
    #[command(hide = true)]
    Python {
        #[command(subcommand)]
        what: PythonCmd,
    },
}

#[derive(Subcommand)]
pub enum PythonCmd {
    /// Download the pinned CPython release into `--cache` with the same
    /// code and SHA-256 check as `vk driver pack`, and leave it there as
    /// `--as FILE` (a bare file name). A copy already cached under that
    /// name is checked and kept, not downloaded again. Runs in this process:
    /// no daemon, no ledger.
    Fetch {
        #[arg(long, value_name = "DIR")]
        cache: PathBuf,
        /// The file name the release is left under, so CI can name it in an
        /// environment variable; no directory in it.
        #[arg(long = "as", value_name = "FILE")]
        file: String,
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
        CapitalCmd::Python { .. } => unreachable!("not a syscall"),
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

/// `vk capital python fetch`: the pinned release in `cache` under `name`,
/// downloaded and hashed by [`vk_capital::python::fetch`] — the pack's own
/// code, so CI trusts exactly what a pack trusts. A copy already left under
/// `name` by an earlier run (a CI cache restore) is put back under the
/// release's own name first, so `fetch` hashes it instead of downloading.
pub fn python_fetch(cache: &Path, name: &str) -> Result<PathBuf> {
    fetched_as(cache, name, vk_capital::python::fetch)
}

fn fetched_as(
    cache: &Path,
    name: &str,
    fetch: impl FnOnce(&Path) -> vk_capital::error::Result<PathBuf>,
) -> Result<PathBuf> {
    if name.is_empty() || name == "." || name == ".." || name.contains(['/', '\\', ':']) {
        anyhow::bail!(
            "--as {name:?} is not a bare file name; remedy: pass a name with no directory, \
             such as release.tar.gz"
        );
    }
    let own = cache.join(vk_capital::python::PINNED.file);
    let target = cache.join(name);
    if target != own && target.is_file() && !own.exists() {
        std::fs::rename(&target, &own)
            .with_context(|| format!("put {} back under the release's name", target.display()))?;
    }
    let got = fetch(cache).map_err(|e| anyhow!("{e}"))?;
    if got != target {
        if target.exists() {
            std::fs::remove_file(&target)
                .with_context(|| format!("replace {}", target.display()))?;
        }
        std::fs::rename(&got, &target)
            .with_context(|| format!("rename {} to {}", got.display(), target.display()))?;
    }
    Ok(target)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake(cache: &Path) -> vk_capital::error::Result<PathBuf> {
        let p = cache.join(vk_capital::python::PINNED.file);
        std::fs::write(&p, b"release")?;
        Ok(p)
    }

    #[test]
    fn the_release_is_left_under_the_name_asked_for() {
        let d = tempfile::tempdir().unwrap();
        let got = fetched_as(d.path(), "release.tar.gz", fake).unwrap();
        assert_eq!(got, d.path().join("release.tar.gz"));
        assert_eq!(std::fs::read(&got).unwrap(), b"release");
        assert!(!d.path().join(vk_capital::python::PINNED.file).exists());
    }

    #[test]
    fn a_copy_cached_under_that_name_is_handed_to_fetch_not_downloaded_again() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("release.tar.gz"), b"cached").unwrap();
        let own = d.path().join(vk_capital::python::PINNED.file);
        let got = fetched_as(d.path(), "release.tar.gz", |c| {
            let p = c.join(vk_capital::python::PINNED.file);
            // What `fetch` sees: the cached bytes, under the release's name.
            assert_eq!(std::fs::read(&p).unwrap(), b"cached");
            Ok(p)
        })
        .unwrap();
        assert_eq!(std::fs::read(got).unwrap(), b"cached");
        assert!(!own.exists());
    }

    #[test]
    fn a_name_with_a_path_in_it_is_refused_before_anything_is_fetched() {
        let d = tempfile::tempdir().unwrap();
        for bad in ["", ".", "..", "a/b", "a\\b", "..\\x", "C:x", "/abs"] {
            let e = fetched_as(d.path(), bad, |_| panic!("fetched for {bad:?}")).unwrap_err();
            let e = format!("{e:#}");
            assert!(
                e.contains("bare file name") && e.contains("remedy:"),
                "{bad:?}: {e}"
            );
        }
    }
}
