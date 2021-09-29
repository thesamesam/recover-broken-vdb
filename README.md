# recover-portage-vdb

## About

In some cases, Portage's internal database (VDB) can become corrupted.

This tool covers cases where ELF metadata (e.g. NEEDED, PROVIDES,
REQUIRES) is malformed/missing.

More detail is provided on the Gentoo [wiki](https://wiki.gentoo.org/wiki/Project:Toolchain/Corrupt_VDB_ELF_files).

## Tools

This project provides two external tools:
1. ```recover-broken-vdb-find-broken.sh``` - to find broken packages (detection)
2. ```recover-broken-vdb``` - to fix the VDB (mitigation/fix)

## Usage

### Check for broken packages

Run the [scan](https://wiki.gentoo.org/wiki/Project:Toolchain/Corrupt_VDB_ELF_files#Check_for_broken_files) tool:
```
$ recover-broken-vdb-find-broken.sh | tee broken_vdb_packages
```

If there's no output, you're done!

### Backup database

```
$ cp -r /var/db/pkg /var/db/pkg.orig
```

### Repair database

First, run the tool in pretend mode (outputs to a temporary directory by default):
```
$ recover-broken-vdb
```

If the output looks correct, run the tool again to make changes to the live
database (or merge the temporary directory with it yourself):
```
$ recover-broken-vdb --output /var/db/pkg
```

### Rebuild affected packages

```
$ emerge --ask --verbose --oneshot ">=app-misc/pax-utils-1.3.3"
$ emerge --ask --verbose --oneshot --usepkg=n $(cat broken_vdb_packages)
```
