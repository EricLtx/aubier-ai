//! The state directory's ACL on Windows (SP1b Task 6 fix round 1, Critical 1).
//!
//! On Unix `paths::private_dir` makes the state directory `0700` and refuses
//! one that others can reach. Windows had no counterpart: the directory was
//! created with `create_dir_all` and took whatever the parent handed down. For
//! `%LOCALAPPDATA%` that is the user's own ACL and the premise holds. For
//! `%ProgramData%` it does not — a fresh folder there inherits
//!
//! ```text
//! BUILTIN\Users:(I)(OI)(CI)(RX)          every local account reads it, and every file in it
//! BUILTIN\Users:(I)(CI)(WD,AD,WEA,WA)    every local account may create files and folders in it
//! CREATOR OWNER:(I)(OI)(CI)(IO)(F)       whoever creates it owns it
//! ```
//!
//! — so a second unprivileged logon could read the register and the whole
//! plaintext ledger, plant a `*.jsonl` segment that `LedgerFs::open` replays
//! (a one-file denial of service) or a `shredded/` tombstone that destroys a
//! blob's key, and, worst, **create the directory before the service does** and
//! own everything the service then writes into it.
//!
//! So a service's state directory is created here instead, with an explicit
//! **protected** DACL — `D:P`, the flag that stops the parent's inheritable
//! entries being merged in — granting Full Control to exactly the accounts the
//! caller names and to nobody else, and `OICI` so that every file and
//! directory the store creates underneath inherits those entries and nothing
//! else. An existing directory is accepted only if its owner and its every ACE
//! name one of those accounts; otherwise the node refuses to start and says
//! which owner or entry stopped it. Setting a DACL after the fact would not
//! do: by then the directory is already somebody else's.
//!
//! **Every comparison here is of SIDs by value** (CI fix, Ruling 35). A
//! descriptor read back is walked entry by entry and each SID is formatted
//! with `ConvertSidToStringSidW`, which always gives the numeric
//! `S-1-5-…` form; the SIDs it is compared against are put through the same
//! function first (`canonical_sid`). What is never compared is the *SDDL
//! rendering* of a descriptor: `ConvertSecurityDescriptorToStringSecurityDescriptorW`
//! prints a well-known SID as its two-letter alias — `SY`, `BA`, and, for the
//! built-in local Administrator, `LA` — so the same entry reads
//! `(A;;FA;;;S-1-5-21-…-500)` on one machine and `(A;;FA;;;LA)` on a CI
//! runner that happens to run as that account, and it prints `GENERIC_ALL`
//! as `FA` once the object's generic mapping has been applied.

use crate::sid::ensure_string_sid;
use anyhow::{bail, ensure, Context, Result};
use std::ffi::c_void;
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use windows::core::{PCWSTR, PWSTR};
use windows::Win32::Foundation::{LocalFree, HANDLE, HLOCAL};
use windows::Win32::Security::Authorization::{
    ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
    ConvertStringSidToSidW, GetNamedSecurityInfoW, GetSecurityInfo, SDDL_REVISION_1,
    SE_FILE_OBJECT, SE_KERNEL_OBJECT,
};
use windows::Win32::Security::{
    AclSizeInformation, GetAce, GetAclInformation, GetSecurityDescriptorControl,
    ACCESS_ALLOWED_ACE, ACL, ACL_SIZE_INFORMATION, DACL_SECURITY_INFORMATION,
    OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID, SECURITY_ATTRIBUTES, SE_DACL_PROTECTED,
};
use windows::Win32::Storage::FileSystem::CreateDirectoryW;

/// `NT AUTHORITY\SYSTEM`.
pub const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
/// `BUILTIN\Administrators`.
pub const ADMINISTRATORS_SID: &str = "S-1-5-32-544";
/// `BUILTIN\Users`: every local account.
pub const USERS_SID: &str = "S-1-5-32-545";
/// `Everyone`.
pub const EVERYONE_SID: &str = "S-1-1-0";
/// `NT AUTHORITY\Authenticated Users`.
pub const AUTHENTICATED_USERS_SID: &str = "S-1-5-11";
/// `NT AUTHORITY\INTERACTIVE`: whoever is logged on at the console.
pub const INTERACTIVE_SID: &str = "S-1-5-4";
/// Every access bit that changes a directory or what is in it: write and
/// append data (add a file, add a subdirectory), write extended attributes
/// and attributes, delete a child, delete, write the DACL, take ownership,
/// and the generic all and write an inherit-only entry keeps unmapped.
const WRITE_BITS: u32 = 0x0000_0002
    | 0x0000_0004
    | 0x0000_0010
    | 0x0000_0040
    | 0x0000_0100
    | 0x0001_0000
    | 0x0004_0000
    | 0x0008_0000
    | 0x1000_0000
    | 0x4000_0000;
/// What `0x1200a9` holds: `READ_CONTROL | SYNCHRONIZE | FILE_READ_DATA |
/// FILE_READ_EA | FILE_EXECUTE | FILE_READ_ATTRIBUTES` — read and execute
/// (list and traverse, on a directory), nothing that writes.
pub const READ_AND_EXECUTE_MASK: u32 = 0x0012_00a9;
/// The DACL of an installed driver's tree (packaging spec §4.2, Task 9):
/// protected, so nothing of `%ProgramFiles%`'s own entries is merged in;
/// the administrators and SYSTEM in full; the built-in users read and
/// execute; every entry inherited by everything below.
pub const READONLY_TREE_SDDL: &str = "D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)";

/// `ACCESS_ALLOWED_ACE_TYPE`.
pub const ACE_ALLOW: u8 = 0;
/// `OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE` — what `OICI` sets.
pub const ACE_OBJECT_AND_CONTAINER_INHERIT: u8 = 0x03;
/// `INHERITED_ACE` — the entry came from the parent rather than being set here.
pub const ACE_INHERITED: u8 = 0x10;
/// `FILE_ALL_ACCESS`. What an `FA` entry holds, and what a `GA` entry holds
/// once a file or pipe object's generic mapping has been applied to it — the
/// kernel stores the mapped mask, which is why the SDDL rendering of a pipe
/// created with `GA` reads `FA`.
pub const FILE_ALL_ACCESS_MASK: u32 = 0x001F_01FF;

/// One entry of a DACL, by value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AceEntry {
    /// `ACE_HEADER.AceType`: [`ACE_ALLOW`] for an allow entry.
    pub kind: u8,
    /// `ACE_HEADER.AceFlags`: inheritance bits, and [`ACE_INHERITED`].
    pub flags: u8,
    /// The access mask as stored, i.e. after generic mapping.
    pub mask: u32,
    /// Canonical `S-1-…`, from `ConvertSidToStringSidW`. Never an SDDL alias.
    pub sid: String,
}

/// An object's owner and DACL, by value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DaclView {
    /// Canonical `S-1-…` of the owner.
    pub owner: String,
    /// `SE_DACL_PROTECTED`: nothing is inherited from the parent.
    pub protected: bool,
    pub entries: Vec<AceEntry>,
}

impl DaclView {
    /// The SIDs the DACL names, in order.
    pub fn sids(&self) -> Vec<&str> {
        self.entries.iter().map(|e| e.sid.as_str()).collect()
    }
}

/// Create `dir` reachable by `allowed` and nobody else, or — if it is already
/// there — refuse unless it already is. Ancestors are created the ordinary
/// way: they hold nothing, and a directory planted at this path is caught by
/// the audit whoever made it.
pub fn create_protected_dir(dir: &Path, allowed: &[&str]) -> Result<()> {
    if dir.exists() {
        return audit_protected_dir(dir, allowed)
            .with_context(|| format!("the state directory {} is not private", dir.display()));
    }
    if let Some(parent) = dir.parent() {
        std::fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    let sddl = protected_sddl(allowed)?;
    let wide_sddl = wide(&sddl);
    let wide_path = wide_os(dir);
    let mut psd = PSECURITY_DESCRIPTOR::default();
    // SAFETY: both strings are NUL-terminated and outlive the calls; `psd` is
    // a live out-parameter, and the descriptor it names is freed once.
    unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            PCWSTR(wide_sddl.as_ptr()),
            SDDL_REVISION_1,
            &mut psd,
            None,
        )
        .with_context(|| format!("the descriptor {sddl:?} is not valid"))?;
        let attrs = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: psd.0,
            bInheritHandle: false.into(),
        };
        let made = CreateDirectoryW(PCWSTR(wide_path.as_ptr()), Some(&attrs));
        LocalFree(Some(HLOCAL(psd.0)));
        made.with_context(|| format!("create {} with a DACL of its own", dir.display()))?;
    }
    audit_protected_dir(dir, allowed)
        .with_context(|| format!("the state directory {} was created wrong", dir.display()))
}

/// Create `dir` — an installed driver's `<name>` or `<version>.part`
/// directory, before anything is materialised in it — with
/// [`READONLY_TREE_SDDL`], so that every file an elevated install writes
/// under it is born with that DACL (packaging Task 9, review fix 1). Never
/// an existing directory: one that is there already, made by another
/// install or planted, is refused with an `AlreadyExists` I/O error and left
/// untouched. Read back afterwards, by value: protected, and exactly those
/// three accounts with those masks. Its parent must exist.
pub fn create_readonly_tree_dir(dir: &Path) -> Result<()> {
    create_tree_dir(dir, READONLY_TREE_SDDL)?;
    let view = path_dacl(dir)?;
    let mut held: Vec<(String, u32)> = view
        .entries
        .iter()
        .map(|e| (e.sid.clone(), e.mask))
        .collect();
    held.sort();
    let mut wanted = vec![
        (ADMINISTRATORS_SID.to_string(), FILE_ALL_ACCESS_MASK),
        (LOCAL_SYSTEM_SID.to_string(), FILE_ALL_ACCESS_MASK),
        (USERS_SID.to_string(), READ_AND_EXECUTE_MASK),
    ];
    wanted.sort();
    ensure!(
        view.protected && held == wanted,
        "{} did not take the read-only tree DACL: {view:?}",
        dir.display()
    );
    Ok(())
}

/// Creates `dir`, which must not exist, with `sddl`'s descriptor: an
/// existing path is an `AlreadyExists` I/O error (checked first, and again
/// by `CreateDirectoryW` itself, which never opens an existing directory).
fn create_tree_dir(dir: &Path, sddl: &str) -> Result<()> {
    let exists = || {
        anyhow::Error::new(std::io::Error::from(std::io::ErrorKind::AlreadyExists))
            .context(format!("{} exists already", dir.display()))
    };
    if std::fs::symlink_metadata(dir).is_ok() {
        return Err(exists());
    }
    let wide_sddl = wide(sddl);
    let wide_path = wide_os(dir);
    let mut psd = PSECURITY_DESCRIPTOR::default();
    // SAFETY: both strings are NUL-terminated and outlive the calls; the
    // descriptor the conversion allocates is freed once.
    let made = unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            PCWSTR(wide_sddl.as_ptr()),
            SDDL_REVISION_1,
            &mut psd,
            None,
        )
        .with_context(|| format!("the descriptor {sddl:?} is not valid"))?;
        let attrs = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: psd.0,
            bInheritHandle: false.into(),
        };
        let made = CreateDirectoryW(PCWSTR(wide_path.as_ptr()), Some(&attrs));
        LocalFree(Some(HLOCAL(psd.0)));
        made
    };
    made.map_err(|e| {
        // A Win32 error comes back as the HRESULT `0x8007xxxx`.
        let code = e.code().0 as u32;
        let io = if code & 0xFFFF_0000 == 0x8007_0000 {
            std::io::Error::from_raw_os_error((code & 0xFFFF) as i32)
        } else {
            std::io::Error::other(e.to_string())
        };
        if io.kind() == std::io::ErrorKind::AlreadyExists {
            exists()
        } else {
            anyhow::Error::new(io).context(format!("create {} read-only", dir.display()))
        }
    })
}

/// The first broad group — Everyone, Authenticated Users, the built-in
/// Users, INTERACTIVE — that owns `path` or that an allow entry of its DACL
/// lets change it or what is under it, by SID and name; `None` when only
/// narrower accounts can. An entry counts whether it applies to `path`
/// itself or only to what is created under it: either lets a
/// non-administrator put something where an elevated install will run it.
pub fn non_admin_writer(path: &Path) -> Result<Option<(String, &'static str)>> {
    const BROAD: [(&str, &str); 4] = [
        (EVERYONE_SID, "Everyone"),
        (AUTHENTICATED_USERS_SID, "Authenticated Users"),
        (USERS_SID, "Users"),
        (INTERACTIVE_SID, "INTERACTIVE"),
    ];
    let named = |sid: &str| {
        BROAD
            .iter()
            .find(|(s, _)| *s == sid)
            .map(|(s, n)| (s.to_string(), *n))
    };
    let view = path_dacl(path)?;
    if let Some(owner) = named(&view.owner) {
        return Ok(Some(owner));
    }
    Ok(view
        .entries
        .iter()
        .filter(|e| e.kind == ACE_ALLOW && e.mask & WRITE_BITS != 0)
        .find_map(|e| named(&e.sid)))
}

/// Sets `sddl`'s DACL on the file or directory `path`, protected when the
/// SDDL says `P`, and lets the OS carry its inheritable entries down to
/// everything under it. The tests' way to give a tree back to its owner.
#[cfg(test)]
fn set_dacl(path: &Path, sddl: &str) -> Result<()> {
    use windows::Win32::Security::Authorization::SetNamedSecurityInfoW;
    use windows::Win32::Security::{
        GetSecurityDescriptorDacl, PROTECTED_DACL_SECURITY_INFORMATION,
    };
    let wide_sddl = wide(sddl);
    let wide_path = wide_os(path);
    let mut psd = PSECURITY_DESCRIPTOR::default();
    // SAFETY: both strings are NUL-terminated and outlive the calls; the ACL
    // pointer points into `psd`, which is freed once, after the last use.
    unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            PCWSTR(wide_sddl.as_ptr()),
            SDDL_REVISION_1,
            &mut psd,
            None,
        )
        .with_context(|| format!("the descriptor {sddl:?} is not valid"))?;
        let mut present = windows::core::BOOL::default();
        let mut defaulted = windows::core::BOOL::default();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let read = GetSecurityDescriptorDacl(psd, &mut present, &mut dacl, &mut defaulted);
        let protected = if sddl.starts_with("D:P") {
            PROTECTED_DACL_SECURITY_INFORMATION
        } else {
            Default::default()
        };
        let set = read.and_then(|()| {
            SetNamedSecurityInfoW(
                PCWSTR(wide_path.as_ptr()),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | protected,
                None,
                None,
                Some(dacl as *const ACL),
                None,
            )
            .ok()
        });
        LocalFree(Some(HLOCAL(psd.0)));
        set.with_context(|| format!("set the DACL of {}", path.display()))
    }
}

/// Whether this process runs elevated: its token is the administrators'
/// full one, not the filtered token UAC gives an administrator's ordinary
/// shell. `vk driver install` sets [`READONLY_TREE_SDDL`] only then: a tree
/// a non-administrator owns is theirs to rewrite whatever its DACL says.
pub fn process_is_elevated() -> Result<bool> {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::Security::{
        GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY,
    };
    use windows::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    // SAFETY: the token handle is closed on every path; the buffer is a
    // `TOKEN_ELEVATION`, whose size is the length passed.
    unsafe {
        let mut token = HANDLE::default();
        OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token)
            .context("open this process's token")?;
        let mut elevation = TOKEN_ELEVATION::default();
        let mut len = 0u32;
        let read = GetTokenInformation(
            token,
            TokenElevation,
            Some((&mut elevation as *mut TOKEN_ELEVATION).cast()),
            std::mem::size_of::<TOKEN_ELEVATION>() as u32,
            &mut len,
        );
        let _ = CloseHandle(token);
        read.context("read whether this process's token is elevated")?;
        Ok(elevation.TokenIsElevated != 0)
    }
}

/// Whether this process is an **elevated administrator**: its token is
/// elevated ([`process_is_elevated`]) *and* holds the local administrators
/// group enabled (`CheckTokenMembership`), which an administrator's filtered
/// UAC token does not — there the group is deny-only.
///
/// Why it matters (CI-4): Windows gives an object the creating token's
/// *default owner*, and for an elevated administrator that is, by policy,
/// the `BUILTIN\Administrators` group rather than the user ("System objects:
/// Default owner for objects created by members of the Administrators
/// group" — the group on Windows Server, the creator on client editions).
/// So a pipe or a directory such a process makes may be owned by
/// `S-1-5-32-544`, and [`owned_by_this_account`] needs to know whether that
/// group is this process's own.
pub fn process_is_elevated_admin() -> Result<bool> {
    use windows::Win32::Security::CheckTokenMembership;
    if !process_is_elevated()? {
        return Ok(false);
    }
    let wide_sid = wide(ADMINISTRATORS_SID);
    // SAFETY: the SID string is NUL-terminated and outlives the call; the SID
    // the conversion allocates is freed once, after the membership check that
    // reads it.
    unsafe {
        let mut sid = PSID::default();
        ConvertStringSidToSidW(PCWSTR(wide_sid.as_ptr()), &mut sid)
            .context("make the administrators' SID")?;
        let mut member = windows::core::BOOL::default();
        let checked = CheckTokenMembership(None, sid, &mut member);
        LocalFree(Some(HLOCAL(sid.0)));
        checked.context("check this process's token for the administrators group")?;
        Ok(member.as_bool())
    }
}

/// Whether an object owned by `owner` was made under this account (`me`, its
/// user SID): `owner` is `me`, or it is the local administrators group and
/// this process is an elevated administrator (`elevated_admin`, from
/// [`process_is_elevated_admin`]) — the default owner Windows gives what
/// such a process creates. SIDs compare case-insensitively, as Windows
/// compares their string forms. A process that is not elevated does not
/// hold the group, so an object it is shown as owned by the administrators
/// is somebody else's.
pub fn owned_by_this_account(owner: &str, me: &str, elevated_admin: bool) -> bool {
    owner.eq_ignore_ascii_case(me)
        || (elevated_admin && owner.eq_ignore_ascii_case(ADMINISTRATORS_SID))
}

/// `D:P(A;OICI;FA;;;<sid>)…` — protected, so nothing is inherited from the
/// parent, and inheritable, so nothing created underneath is reachable by
/// anybody else either. This is a string this crate *writes*; it is never
/// what a read-back is compared against.
pub fn protected_sddl(allowed: &[&str]) -> Result<String> {
    ensure!(
        !allowed.is_empty(),
        "a state directory nobody may reach is one the node cannot open"
    );
    let mut sddl = String::from("D:P");
    for sid in allowed {
        ensure_string_sid(sid)?;
        sddl.push_str("(A;OICI;FA;;;");
        sddl.push_str(sid);
        sddl.push(')');
    }
    Ok(sddl)
}

/// The owner and every entry of `dir`'s DACL must name one of `allowed`,
/// compared **by value**: the object's SIDs come out of `path_dacl` in their
/// canonical numeric form, and `allowed` is canonicalised the same way before
/// the comparison — so an entry the OS would render as `LA`, and an `allowed`
/// written as `BA` rather than `S-1-5-32-544`, still meet.
pub fn audit_protected_dir(dir: &Path, allowed: &[&str]) -> Result<()> {
    let view = path_dacl(dir)?;
    let canonical: Vec<String> = allowed
        .iter()
        .map(|s| canonical_sid(s).with_context(|| format!("{s:?} in the allowed list")))
        .collect::<Result<_>>()?;
    let permitted = |sid: &str| canonical.iter().any(|a| a == sid);
    ensure!(
        permitted(&view.owner),
        "it is owned by {}, which is neither this account, nor SYSTEM, nor the local \
         administrators — somebody else created it first, so everything written into it would be \
         theirs. Move it aside, or delete it and let the node make it.",
        view.owner
    );
    if let Some(stranger) = view.entries.iter().find(|e| !permitted(&e.sid)) {
        bail!(
            "its DACL has an entry for {}, which is not this account, SYSTEM or the local \
             administrators. Reset it (`icacls \"{dir}\" /inheritance:r /grant *{first}:(OI)(CI)F`, \
             then the other accounts), or delete the directory and let the node make it.",
            stranger.sid,
            dir = dir.display(),
            first = allowed[0]
        );
    }
    Ok(())
}

/// The canonical `S-1-…` form of a SID given as a string — numeric already, or
/// an SDDL alias such as `SY`, `BA` or `LA` — by converting it to the binary
/// SID and back. Two strings name the same account exactly when their
/// canonical forms are equal; that is the only SID comparison this module
/// makes.
pub fn canonical_sid(sid: &str) -> Result<String> {
    let wide_sid = wide(sid);
    // SAFETY: the string is NUL-terminated and outlives the call; the binary
    // SID the conversion allocates is formatted and then freed exactly once.
    unsafe {
        let mut psid = PSID::default();
        ConvertStringSidToSidW(PCWSTR(wide_sid.as_ptr()), &mut psid)
            .with_context(|| format!("{sid:?} is not a SID Windows can read"))?;
        let text = sid_to_string(psid);
        LocalFree(Some(HLOCAL(psid.0)));
        text
    }
}

/// The owner and DACL of a file or directory, by value.
pub fn path_dacl(path: &Path) -> Result<DaclView> {
    Descriptor::of_path(path)?.view()
}

/// The owner and DACL of an open kernel handle — a named pipe instance, in
/// this workspace — by value. The handle needs `READ_CONTROL`, which the
/// creator of an object always has on its own handle.
pub fn handle_dacl(handle: std::os::windows::io::RawHandle) -> Result<DaclView> {
    Descriptor::of_handle(HANDLE(handle))?.view()
}

/// A `LocalAlloc`-ed security descriptor, with the owner and DACL pointers
/// that live inside it.
struct Descriptor {
    psd: PSECURITY_DESCRIPTOR,
    owner: PSID,
    dacl: *const ACL,
}

impl Descriptor {
    fn of_path(path: &Path) -> Result<Descriptor> {
        let wide_path = wide_os(path);
        let mut owner = PSID::default();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut psd = PSECURITY_DESCRIPTOR::default();
        // SAFETY: the path is NUL-terminated and outlives the call; the three
        // out-parameters are live, and the descriptor is freed in `Drop`.
        let rc = unsafe {
            GetNamedSecurityInfoW(
                PCWSTR(wide_path.as_ptr()),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                Some(&mut owner),
                None,
                Some(&mut dacl),
                None,
                &mut psd,
            )
        };
        ensure!(
            rc.is_ok(),
            "read the owner and DACL of {}: {rc:?}",
            path.display()
        );
        Ok(Descriptor {
            psd,
            owner,
            dacl: dacl as *const ACL,
        })
    }

    fn of_handle(handle: HANDLE) -> Result<Descriptor> {
        let mut owner = PSID::default();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut psd = PSECURITY_DESCRIPTOR::default();
        // SAFETY: the caller's handle is live for the call; the out-parameters
        // are live, and the descriptor is freed in `Drop`.
        let rc = unsafe {
            GetSecurityInfo(
                handle,
                SE_KERNEL_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                Some(&mut owner),
                None,
                Some(&mut dacl),
                None,
                Some(&mut psd),
            )
        };
        ensure!(rc.is_ok(), "read the owner and DACL of a handle: {rc:?}");
        Ok(Descriptor {
            psd,
            owner,
            dacl: dacl as *const ACL,
        })
    }

    /// Walk the descriptor into a `DaclView`: the owner, the protection bit,
    /// and every entry with its type, flags, mask and canonical SID.
    fn view(&self) -> Result<DaclView> {
        // SAFETY: `self` keeps the descriptor — and therefore the owner SID
        // and the ACL, which point into it — alive for the whole block.
        unsafe {
            let owner = sid_to_string(self.owner)?;
            let mut control = 0u16;
            let mut revision = 0u32;
            GetSecurityDescriptorControl(self.psd, &mut control, &mut revision)
                .context("read the descriptor's control bits")?;
            let protected = control & SE_DACL_PROTECTED.0 != 0;
            ensure!(
                !self.dacl.is_null(),
                "it has no DACL at all, which on Windows means every account has every access"
            );
            let mut size = ACL_SIZE_INFORMATION::default();
            GetAclInformation(
                self.dacl,
                &mut size as *mut _ as *mut c_void,
                std::mem::size_of::<ACL_SIZE_INFORMATION>() as u32,
                AclSizeInformation,
            )
            .context("read the size of its DACL")?;
            let mut entries = Vec::with_capacity(size.AceCount as usize);
            for i in 0..size.AceCount {
                let mut ace: *mut c_void = std::ptr::null_mut();
                GetAce(self.dacl, i, &mut ace)
                    .with_context(|| format!("read entry {i} of its DACL"))?;
                // Every entry that names a principal — allow, deny, or their
                // callback forms — carries the SID straight after the header
                // and the mask, which is what `ACCESS_ALLOWED_ACE` lays out; a
                // deny entry for somebody else is as much of a surprise as an
                // allow one, so both are collected. The *object* ACE forms put
                // a GUID there instead, and this cast would misread one — but
                // they exist only in directory-service DACLs, never on a file
                // or pipe object, which is all this module looks at.
                let ace = &*(ace as *const ACCESS_ALLOWED_ACE);
                entries.push(AceEntry {
                    kind: ace.Header.AceType,
                    flags: ace.Header.AceFlags,
                    mask: ace.Mask,
                    sid: sid_to_string(PSID(&ace.SidStart as *const u32 as *mut c_void))?,
                });
            }
            Ok(DaclView {
                owner,
                protected,
                entries,
            })
        }
    }
}

impl Drop for Descriptor {
    fn drop(&mut self) {
        // SAFETY: the pointer came from `GetNamedSecurityInfoW` or
        // `GetSecurityInfo`, both of which document `LocalFree` as the way to
        // release it, exactly once.
        unsafe { LocalFree(Some(HLOCAL(self.psd.0))) };
    }
}

/// The canonical numeric form of a binary SID. `ConvertSidToStringSidW`
/// never produces an SDDL alias, which is what makes its output safe to
/// compare.
///
/// SAFETY: `sid` must point at a valid SID that outlives the call.
unsafe fn sid_to_string(sid: PSID) -> Result<String> {
    unsafe {
        let mut text = PWSTR::null();
        ConvertSidToStringSidW(sid, &mut text).context("format a SID")?;
        let s = text.to_string().context("a SID is not UTF-16");
        LocalFree(Some(HLOCAL(text.0 as *mut c_void)));
        s
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn wide_os(p: &Path) -> Vec<u16> {
    p.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

/// This process's token user, as a string SID. `vk-ipc` has the same lookup
/// for the pipe's DACL, but it depends on this crate, so the dependency cannot
/// run the other way.
pub fn current_process_sid() -> Result<String> {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::Security::{GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER};
    use windows::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    // SAFETY: the token handle is closed on every path, the buffer is sized by
    // the OS, and its length is checked before the cast.
    unsafe {
        let mut token = HANDLE::default();
        OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token)
            .context("open this process's token")?;
        let mut needed = 0u32;
        // Expected to fail with ERROR_INSUFFICIENT_BUFFER; `needed` is the point.
        let _ = GetTokenInformation(token, TokenUser, None, 0, &mut needed);
        let mut buf = vec![0u8; needed as usize];
        let read = GetTokenInformation(
            token,
            TokenUser,
            Some(buf.as_mut_ptr().cast()),
            needed,
            &mut needed,
        );
        let _ = CloseHandle(token);
        read.context("read this process's token user")?;
        ensure!(
            buf.len() >= std::mem::size_of::<TOKEN_USER>(),
            "the token user is shorter than a TOKEN_USER"
        );
        let user = &*(buf.as_ptr() as *const TOKEN_USER);
        sid_to_string(user.User.Sid)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;
    use std::sync::atomic::{AtomicU32, Ordering};

    static NEXT: AtomicU32 = AtomicU32::new(0);

    fn temp_path(tag: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "vk-acl-{tag}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }

    fn me_system_admins() -> (String, [String; 3]) {
        let me = current_process_sid().unwrap();
        let all = [
            me.clone(),
            LOCAL_SYSTEM_SID.to_string(),
            ADMINISTRATORS_SID.to_string(),
        ];
        (me, all)
    }

    fn refs(all: &[String; 3]) -> Vec<&str> {
        all.iter().map(String::as_str).collect()
    }

    fn set_of<'a>(it: impl IntoIterator<Item = &'a str>) -> BTreeSet<String> {
        it.into_iter().map(str::to_string).collect()
    }

    /// The SDDL *rendering* of a descriptor, which the tests below use only to
    /// show what a string comparison would have seen — never to decide.
    fn rendered_dacl(path: &Path) -> String {
        use windows::Win32::Security::Authorization::ConvertSecurityDescriptorToStringSecurityDescriptorW;
        let d = Descriptor::of_path(path).unwrap();
        unsafe {
            let mut text = PWSTR::null();
            ConvertSecurityDescriptorToStringSecurityDescriptorW(
                d.psd,
                SDDL_REVISION_1,
                DACL_SECURITY_INFORMATION,
                &mut text,
                None,
            )
            .unwrap();
            let s = text.to_string().unwrap();
            LocalFree(Some(HLOCAL(text.0 as *mut c_void)));
            s
        }
    }

    #[test]
    fn the_directory_is_created_with_exactly_those_accounts_and_inherits_nothing() {
        let (_, all) = me_system_admins();
        let allowed = refs(&all);
        let dir = temp_path("made");
        create_protected_dir(&dir, &allowed).unwrap();
        let view = path_dacl(&dir).unwrap();
        // Protected: nothing from `%TEMP%`, or `%ProgramData%`, merged in.
        assert!(view.protected, "{view:?}");
        // Exactly the three accounts, compared as SIDs — so the Users group,
        // Everyone, Authenticated Users, Anonymous and Creator Owner that the
        // parent would have handed down are absent by construction, and it
        // does not matter how the OS would *render* this account (`LA` for the
        // built-in Administrator a CI runner runs as).
        assert_eq!(
            set_of(view.sids()),
            set_of(allowed.iter().copied()),
            "{view:?}"
        );
        assert_eq!(view.entries.len(), 3, "{view:?}");
        for e in &view.entries {
            assert_eq!(e.kind, ACE_ALLOW, "{e:?}");
            assert_eq!(e.mask, FILE_ALL_ACCESS_MASK, "{e:?}");
            assert_eq!(
                e.flags & ACE_OBJECT_AND_CONTAINER_INHERIT,
                ACE_OBJECT_AND_CONTAINER_INHERIT,
                "every entry is inheritable: {e:?}"
            );
            assert_eq!(e.flags & ACE_INHERITED, 0, "set here, not inherited: {e:?}");
        }
        // The audit agrees, and a child inherits the same list — which is what
        // protects the ledger, the register and the blobs.
        audit_protected_dir(&dir, &allowed).unwrap();
        let child = dir.join("ledger");
        std::fs::create_dir(&child).unwrap();
        let child_view = path_dacl(&child).unwrap();
        assert_eq!(set_of(child_view.sids()), set_of(allowed.iter().copied()));
        assert!(
            child_view
                .entries
                .iter()
                .all(|e| e.flags & ACE_INHERITED != 0),
            "a child's entries are the parent's, inherited: {child_view:?}"
        );
        audit_protected_dir(&child, &allowed).expect("a child inherits the same entries");
        std::fs::remove_dir_all(&dir).ok();
    }

    /// The CI failure, reproduced on any machine (Ruling 35). An entry for the
    /// built-in local Administrator is *rendered* `LA`, SYSTEM `SY`, the
    /// administrators `BA`; the same accounts compared by value are the
    /// numeric SIDs. A descriptor written with the aliases must be accepted by
    /// an audit written with the numbers, and the reverse, and the alias and
    /// numeric forms must be one SID to `EqualSid` as well as to
    /// `canonical_sid`.
    #[test]
    fn an_alias_and_its_numeric_sid_are_the_same_account() {
        use windows::Win32::Security::EqualSid;
        let me = current_process_sid().unwrap();
        let la = canonical_sid("LA").expect("the built-in Administrator has a SID here");
        assert!(la.starts_with("S-1-5-21-") && la.ends_with("-500"), "{la}");
        assert_eq!(canonical_sid("SY").unwrap(), LOCAL_SYSTEM_SID);
        assert_eq!(canonical_sid("BA").unwrap(), ADMINISTRATORS_SID);
        assert_eq!(
            canonical_sid(&la).unwrap(),
            la,
            "canonical is a fixed point"
        );
        assert_ne!(canonical_sid("SY").unwrap(), canonical_sid("BA").unwrap());

        // `EqualSid` says the same thing about the same pairs.
        let binary = |s: &str| {
            let w = wide(s);
            let mut p = PSID::default();
            unsafe { ConvertStringSidToSidW(PCWSTR(w.as_ptr()), &mut p).unwrap() };
            p
        };
        for (alias, numeric, same) in [
            ("LA", la.as_str(), true),
            ("SY", LOCAL_SYSTEM_SID, true),
            ("BA", ADMINISTRATORS_SID, true),
            ("SY", ADMINISTRATORS_SID, false),
        ] {
            let (a, b) = (binary(alias), binary(numeric));
            let equal = unsafe { EqualSid(a, b).is_ok() };
            unsafe {
                LocalFree(Some(HLOCAL(a.0)));
                LocalFree(Some(HLOCAL(b.0)));
            }
            assert_eq!(equal, same, "EqualSid({alias}, {numeric})");
            assert_eq!(
                canonical_sid(alias).unwrap() == canonical_sid(numeric).unwrap(),
                same,
                "canonical_sid agrees with EqualSid for {alias} / {numeric}"
            );
        }

        // A descriptor written with the aliases…
        let dir = temp_path("aliases");
        create_with_sddl(
            &dir,
            &format!("D:P(A;OICI;FA;;;{me})(A;OICI;FA;;;LA)(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)"),
        );
        // …renders with the aliases, which is exactly what a string comparison
        // would have tripped on…
        let rendered = rendered_dacl(&dir);
        assert!(rendered.contains(";LA)"), "{rendered}");
        assert!(!rendered.contains(&format!(";{la})")), "{rendered}");
        // …reads back by value as the numbers…
        let view = path_dacl(&dir).unwrap();
        assert_eq!(
            set_of(view.sids()),
            set_of([
                me.as_str(),
                la.as_str(),
                ADMINISTRATORS_SID,
                LOCAL_SYSTEM_SID
            ])
        );
        // …and is accepted by an audit written either way.
        audit_protected_dir(&dir, &[&me, &la, ADMINISTRATORS_SID, LOCAL_SYSTEM_SID])
            .expect("numeric allow list, aliased descriptor");
        audit_protected_dir(&dir, &[&me, "LA", "BA", "SY"])
            .expect("aliased allow list, aliased descriptor");
        // Taking the built-in Administrator off the list is still a refusal,
        // naming it by number — unless this test *is* the built-in
        // Administrator (a GitHub Windows runner is), in which case `me` is
        // `LA` and keeping `me` keeps it.
        if me != la {
            let err = format!(
                "{:#}",
                audit_protected_dir(&dir, &[&me, ADMINISTRATORS_SID, LOCAL_SYSTEM_SID])
                    .unwrap_err()
            );
            assert!(err.contains(&la), "{err}");
        }
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_directory_a_second_account_could_reach_is_refused_by_name() {
        let (me, all) = me_system_admins();
        let allowed = refs(&all);
        let dir = temp_path("planted");
        // What a second local logon's pre-creation looks like from here: us,
        // plus an entry for the Users group. Staging it under our own name is
        // the only way to do this without a second account; what is under test
        // is the entry check, and `BU` is a principal that is not on the list.
        create_with_sddl(&dir, &format!("D:P(A;OICI;FA;;;{me})(A;OICI;FA;;;BU)"));
        let err = format!("{:#}", create_protected_dir(&dir, &allowed).unwrap_err());
        assert!(err.contains("not private"), "{err}");
        assert!(err.contains("S-1-5-32-545"), "{err}");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn an_existing_directory_that_is_already_private_is_accepted() {
        let (_, all) = me_system_admins();
        let allowed = refs(&all);
        let dir = temp_path("again");
        create_protected_dir(&dir, &allowed).unwrap();
        // The second start of the same service.
        create_protected_dir(&dir, &allowed).unwrap();
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_sddl_is_protected_and_inheritable_and_takes_string_sids_only() {
        assert_eq!(
            protected_sddl(&["S-1-5-18", "S-1-5-32-544"]).unwrap(),
            "D:P(A;OICI;FA;;;S-1-5-18)(A;OICI;FA;;;S-1-5-32-544)"
        );
        assert!(protected_sddl(&[]).is_err());
        assert!(protected_sddl(&["BA"]).is_err());
    }

    #[test]
    fn this_process_has_a_string_sid() {
        let sid = current_process_sid().unwrap();
        crate::sid::ensure_string_sid(&sid).unwrap();
        assert_eq!(canonical_sid(&sid).unwrap(), sid, "already canonical");
    }

    /// The DACL an installed driver's tree is born with (packaging Task 9,
    /// review fix 1): protected, the administrators and SYSTEM in full, the
    /// built-in users read and execute, every entry inheritable — set when
    /// the directory is created, never on one that exists, so that what an
    /// elevated install materialises under it is read-only to every other
    /// account from its first byte, and a directory somebody else made
    /// first is refused rather than adopted.
    #[test]
    fn an_installed_tree_is_born_read_only_and_never_adopts_a_directory() {
        let expected = set_of_entries([
            (ADMINISTRATORS_SID, FILE_ALL_ACCESS_MASK),
            (LOCAL_SYSTEM_SID, FILE_ALL_ACCESS_MASK),
            (USERS_SID, READ_AND_EXECUTE_MASK),
        ]);
        let fresh = temp_path("fresh");
        create_readonly_tree_dir(&fresh).unwrap();
        let view = path_dacl(&fresh).unwrap();
        assert!(view.protected, "{view:?}");
        assert_eq!(entries_of(&view), expected, "{view:?}");
        for e in &view.entries {
            assert_eq!(e.kind, ACE_ALLOW, "{e:?}");
            assert_eq!(
                e.flags & ACE_OBJECT_AND_CONTAINER_INHERIT,
                ACE_OBJECT_AND_CONTAINER_INHERIT,
                "inheritable: {e:?}"
            );
            assert_eq!(e.flags & ACE_INHERITED, 0, "set here: {e:?}");
        }
        let exists = |dir: &Path| {
            let e = create_readonly_tree_dir(dir).unwrap_err();
            assert_eq!(
                e.downcast_ref::<std::io::Error>().map(std::io::Error::kind),
                Some(std::io::ErrorKind::AlreadyExists),
                "{e:#}"
            );
        };
        exists(&fresh);
        let planted = temp_path("planted");
        std::fs::create_dir(&planted).unwrap();
        let before = path_dacl(&planted).unwrap();
        exists(&planted);
        assert_eq!(path_dacl(&planted).unwrap(), before, "untouched");

        // What is made under it takes its entries. This process is no
        // administrator, so the tree it makes here names it too, to write.
        let me = current_process_sid().unwrap();
        let tree = temp_path("tree");
        create_tree_dir(&tree, &format!("{READONLY_TREE_SDDL}(A;OICI;FA;;;{me})")).unwrap();
        std::fs::create_dir_all(tree.join("python")).unwrap();
        std::fs::write(tree.join("python").join("python.exe"), b"MZ").unwrap();
        std::fs::write(tree.join("driver.py"), b"print(1)").unwrap();
        for child in [
            tree.join("driver.py"),
            tree.join("python"),
            tree.join("python").join("python.exe"),
        ] {
            let view = path_dacl(&child).unwrap();
            assert!(
                entries_of(&view).is_superset(&expected),
                "{child:?}: {view:?}"
            );
            assert!(
                view.entries.iter().all(|e| e.flags & ACE_INHERITED != 0),
                "{child:?} takes the tree's entries: {view:?}"
            );
        }
        // Its owner may always rewrite a DACL: give the test its trees back.
        set_dacl(&fresh, &format!("D:P(A;OICI;FA;;;{me})")).unwrap();
        for dir in [&fresh, &planted, &tree] {
            std::fs::remove_dir_all(dir).unwrap();
        }
    }

    /// The root an elevated install writes under must be the
    /// administrators' alone: an allow entry that lets Everyone,
    /// Authenticated Users, the built-in Users or INTERACTIVE change it — on
    /// the root itself or only on what is created under it — is named.
    #[test]
    fn a_root_a_broad_group_may_write_is_named() {
        let me = current_process_sid().unwrap();
        for (sddl, writer) in [
            (
                format!("D:P(A;OICI;FA;;;{me})(A;OICI;0x1301bf;;;AU)"),
                Some((AUTHENTICATED_USERS_SID, "Authenticated Users")),
            ),
            (
                format!("D:P(A;OICI;FA;;;{me})(A;;0x4;;;BU)"),
                Some((USERS_SID, "Users")),
            ),
            (
                format!("D:P(A;OICI;FA;;;{me})(A;OICIIO;GW;;;WD)"),
                Some((EVERYONE_SID, "Everyone")),
            ),
            (
                format!("D:P(A;OICI;FA;;;{me})(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"),
                None,
            ),
        ] {
            let dir = temp_path("root");
            create_with_sddl(&dir, &sddl);
            let named = non_admin_writer(&dir).unwrap();
            assert_eq!(
                named,
                writer.map(|(sid, name)| (sid.to_string(), name)),
                "{sddl}"
            );
            std::fs::remove_dir_all(&dir).unwrap();
        }
    }

    #[test]
    fn whether_this_process_is_elevated_can_be_read() {
        process_is_elevated().unwrap();
    }

    /// An elevated administrator is an elevated process first: the probe
    /// answers, and never says yes for a process that is not elevated.
    #[test]
    fn whether_this_process_is_an_elevated_administrator_can_be_read() {
        let admin = process_is_elevated_admin().unwrap();
        assert!(
            !admin || process_is_elevated().unwrap(),
            "an elevated administrator's token is elevated"
        );
    }

    /// The owner rule (CI-4): an object of this account's is owned by its
    /// user SID, or — when this process is an elevated administrator, whose
    /// token's default owner Windows may set to the group — by the local
    /// administrators. Anything else is somebody else's, and the group is
    /// not this account's when this process is not elevated.
    #[test]
    fn an_object_is_this_accounts_when_its_owner_is_this_user_or_its_elevated_group() {
        let me = "S-1-5-21-1111111111-2222222222-3333333333-1001";
        // Owner == this user: always.
        assert!(owned_by_this_account(me, me, false));
        assert!(owned_by_this_account(me, me, true));
        assert!(owned_by_this_account(&me.to_lowercase(), me, false));
        // Owner == the administrators, and this process is an elevated
        // administrator: the token's default owner.
        assert!(owned_by_this_account(ADMINISTRATORS_SID, me, true));
        // Owner == the administrators, and this process is not elevated:
        // not this account's object.
        assert!(!owned_by_this_account(ADMINISTRATORS_SID, me, false));
        for stranger in [
            "S-1-5-21-1111111111-2222222222-3333333333-1002",
            LOCAL_SYSTEM_SID,
            USERS_SID,
            "",
        ] {
            assert!(!owned_by_this_account(stranger, me, true), "{stranger}");
            assert!(!owned_by_this_account(stranger, me, false), "{stranger}");
        }
    }

    fn set_of_entries<'a>(it: impl IntoIterator<Item = (&'a str, u32)>) -> BTreeSet<(String, u32)> {
        it.into_iter().map(|(s, m)| (s.to_string(), m)).collect()
    }

    fn entries_of(view: &DaclView) -> BTreeSet<(String, u32)> {
        view.entries
            .iter()
            .map(|e| (e.sid.clone(), e.mask))
            .collect()
    }

    fn create_with_sddl(dir: &Path, sddl: &str) {
        let wide_sddl = wide(sddl);
        let wide_path = wide_os(dir);
        unsafe {
            let mut psd = PSECURITY_DESCRIPTOR::default();
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                PCWSTR(wide_sddl.as_ptr()),
                SDDL_REVISION_1,
                &mut psd,
                None,
            )
            .unwrap();
            let attrs = SECURITY_ATTRIBUTES {
                nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
                lpSecurityDescriptor: psd.0,
                bInheritHandle: false.into(),
            };
            CreateDirectoryW(PCWSTR(wide_path.as_ptr()), Some(&attrs)).unwrap();
            LocalFree(Some(HLOCAL(psd.0)));
        }
    }
}
