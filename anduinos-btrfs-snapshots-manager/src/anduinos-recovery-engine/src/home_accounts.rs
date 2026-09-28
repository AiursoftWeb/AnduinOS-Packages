//! Conservative account/ownership checks before replacing the entire Home.
//! No passwd/shadow contents are persisted in snapshot metadata or diagnostics.
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Read;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;

pub fn verify_home_accounts(passwd: &Path, home: &Path) -> Result<(), String> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(passwd)
        .map_err(|_| "Cannot read the account database for Home rollback".to_string())?;
    if !file
        .metadata()
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("The account database is not a regular file".into());
    }
    let mut contents = String::new();
    file.take(1024 * 1024 + 1)
        .read_to_string(&mut contents)
        .map_err(|error| error.to_string())?;
    if contents.len() > 1024 * 1024 {
        return Err("The account database is too large".into());
    }
    verify_contents(&contents, home)
}

fn verify_contents(passwd: &str, home: &Path) -> Result<(), String> {
    let mut accounts = BTreeMap::new();
    for line in passwd.lines().filter(|line| !line.is_empty()) {
        let fields: Vec<_> = line.split(':').collect();
        if fields.len() != 7 {
            return Err("Invalid account database".into());
        }
        let Some(name) = fields[5].strip_prefix("/home/") else {
            continue;
        };
        if name.is_empty() || name.contains('/') || name == "." || name == ".." {
            return Err("Home rollback requires direct, local user directories under /home".into());
        }
        let uid = fields[2]
            .parse::<u32>()
            .map_err(|_| "Invalid account UID")?;
        let gid = fields[3]
            .parse::<u32>()
            .map_err(|_| "Invalid account GID")?;
        if accounts.insert(name.to_owned(), (uid, gid)).is_some() {
            return Err("Multiple accounts share a Home directory".into());
        }
        let metadata = fs::symlink_metadata(home.join(name))
            .map_err(|_| format!("The Home snapshot has no directory for {name}; restore a matching system snapshot first"))?;
        if !metadata.is_dir() || metadata.uid() != uid || metadata.gid() != gid {
            return Err(format!(
                "The Home snapshot ownership does not match account {name}; restore a matching system snapshot first"
            ));
        }
    }
    for entry in fs::read_dir(home).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|error| error.to_string())?;
        // Unknown user-owned entries could expose a deleted user's files to a
        // reused UID. Root-owned shared directories are not account homes.
        if metadata.uid() != 0
            && !accounts.contains_key(&entry.file_name().to_string_lossy().into_owned())
        {
            return Err("The Home snapshot contains data for accounts absent from the system; restore a matching system snapshot first".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matching_accounts_are_required_and_symlinks_are_rejected() {
        let root = std::env::temp_dir().join(format!("home-accounts-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join("alice")).unwrap();
        let meta = fs::metadata(root.join("alice")).unwrap();
        let passwd = format!(
            "alice:x:{}:{}::/home/alice:/bin/bash\n",
            meta.uid(),
            meta.gid()
        );
        assert!(verify_contents(&passwd, &root).is_ok());
        assert!(verify_contents(&passwd.replace("/home/alice", "/home/bob"), &root).is_err());
        assert!(
            verify_contents(
                &passwd.replace(&format!(":{}:", meta.uid()), ":4294967294:"),
                &root
            )
            .is_err()
        );
        fs::rename(root.join("alice"), root.join("real")).unwrap();
        std::os::unix::fs::symlink("real", root.join("alice")).unwrap();
        assert!(verify_contents(&passwd, &root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
