//! `materialise` end to end with a real python-build-standalone archive
//! (`VK_TEST_PYTHON_RELEASE` names the local `install_only` .tar.gz; the test
//! skips without it), and the tar and release-table checks that need none.

use std::path::PathBuf;

fn release() -> Option<PathBuf> {
    std::env::var_os("VK_TEST_PYTHON_RELEASE").map(PathBuf::from)
}

#[test]
fn the_same_inputs_materialise_to_one_fingerprint_in_two_directories() {
    let Some(archive) = release() else {
        eprintln!("VK_TEST_PYTHON_RELEASE unset: skipped");
        return;
    };
    let t = tempfile::tempdir().unwrap();
    // a two-file driver and a requirements with one pure wheel that has a
    // console script (`pip-install-test` 0.5, the plan's choice, has none)
    let src = t.path().join("src");
    std::fs::create_dir_all(&src).unwrap();
    std::fs::write(
        src.join("driver.py"),
        b"import pycodestyle\nprint('hello')\n",
    )
    .unwrap();
    std::fs::write(src.join("requirements.txt"), b"pycodestyle==2.12.1\n").unwrap();
    let files_tar = vk_capital::wheels::tar_dir(&src).unwrap();
    let pinned = vk_capital::python::PINNED.sha256;
    let py = vk_capital::python::extract(&archive, &t.path().join("stage"), pinned).unwrap();
    let wheels_dir = t.path().join("wheels");
    let lock = vk_capital::wheels::resolve(
        &py.join("python.exe"),
        &src.join("requirements.txt"),
        &wheels_dir,
    )
    .unwrap();
    assert_eq!(lock.len(), 1);
    let wheels_tar = vk_capital::wheels::tar_dir(&wheels_dir).unwrap();
    let a = vk_capital::materialise::materialise(
        &t.path().join("a"),
        &files_tar,
        &archive,
        pinned,
        &wheels_tar,
        &lock,
    )
    .unwrap();
    let b = vk_capital::materialise::materialise(
        &t.path().join("b"),
        &files_tar,
        &archive,
        pinned,
        &wheels_tar,
        &lock,
    )
    .unwrap();
    assert_eq!(a.fingerprint, b.fingerprint);
    // the launcher pip wrote is gone and its RECORD row with it, no
    // direct_url.json names the scratch wheels, the module is importable with -I
    assert!(!a.root.join("python/Scripts/pycodestyle.exe").exists());
    let info = a
        .root
        .join("python/Lib/site-packages/pycodestyle-2.12.1.dist-info");
    let record = std::fs::read_to_string(info.join("RECORD")).unwrap();
    assert!(!record.contains("Scripts/"), "{record}");
    assert!(!info.join("direct_url.json").exists());
    let out = std::process::Command::new(a.root.join("python/python.exe"))
        .args(["-I", "driver.py"])
        .current_dir(&a.root)
        .output()
        .unwrap();
    assert!(
        String::from_utf8_lossy(&out.stdout).contains("hello"),
        "{:?}",
        out
    );
}

#[test]
fn a_tar_entry_that_escapes_is_refused() {
    let mut buf = Vec::new();
    {
        let mut b = tar::Builder::new(&mut buf);
        let data = b"x";
        let mut h = tar::Header::new_gnu();
        h.set_size(1);
        h.set_mode(0o644);
        // `append_data` refuses a `..` path: a hostile archive writes it raw.
        let name = b"../escape.txt";
        h.as_gnu_mut().unwrap().name[..name.len()].copy_from_slice(name);
        h.set_cksum();
        b.append(&h, &data[..]).unwrap();
        b.finish().unwrap();
    }
    let t = tempfile::tempdir().unwrap();
    assert!(vk_capital::wheels::untar_into(&buf, t.path())
        .unwrap_err()
        .to_string()
        .contains("escape"));
}

#[test]
fn tar_dir_is_deterministic() {
    let t = tempfile::tempdir().unwrap();
    std::fs::write(t.path().join("b.txt"), b"2").unwrap();
    std::fs::write(t.path().join("a.txt"), b"1").unwrap();
    let x = vk_capital::wheels::tar_dir(t.path()).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(1100));
    std::fs::write(t.path().join("a.txt"), b"1").unwrap(); // new mtime, same bytes
    assert_eq!(x, vk_capital::wheels::tar_dir(t.path()).unwrap());
}

#[test]
fn the_pinned_release_table_is_well_formed() {
    let r = &vk_capital::python::PINNED;
    assert!(r
        .file
        .ends_with("-x86_64-pc-windows-msvc-install_only.tar.gz"));
    assert_eq!(r.sha256.len(), 64);
    assert!(r
        .url
        .starts_with("https://github.com/astral-sh/python-build-standalone/releases/download/"));
}

#[test]
#[ignore = "network: downloads the pinned CPython release"]
fn the_pinned_release_downloads_and_matches_its_sha256() {
    let t = tempfile::tempdir().unwrap();
    let p = vk_capital::python::fetch(t.path()).unwrap();
    assert!(p.exists());
}
