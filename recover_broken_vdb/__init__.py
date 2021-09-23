#!/usr/bin/env python3
import argparse
import re
import pathlib
import tempfile
import subprocess
import sys

from portage.util._dyn_libs.NeededEntry import NeededEntry
from portage.util._dyn_libs.soname_deps import SonameDepsProcessor

# Nabbed from Portage's LinkageMapELF
_approx_multilib_categories = {
    "386": "x86_32",
    "68K": "m68k_32",
    "AARCH64": "arm_64",
    "ALPHA": "alpha_64",
    "ARM": "arm_32",
    "IA_64": "ia64_64",
    "MIPS": "mips_o32",
    "PARISC": "hppa_64",
    "PPC": "ppc_32",
    "PPC64": "ppc_64",
    "S390": "s390_64",
    "SH": "sh_32",
    "SPARC": "sparc_32",
    "SPARC32PLUS": "sparc_32",
    "SPARCV9": "sparc_64",
    "X86_64": "x86_64",
}


class BrokenPackage:
    """
    Represents packages with bad state in the VDB.

    Attributes
    ----------
    cpf : str
        ${CATEGORY}/${PF} - category/revision
    contents : list
        list of the installed files
    dyn_paths: list
        list of the installed dynamic libraries to check for integrity
    """

    def __init__(self, cpf, contents=None, dyn_paths=None):
        self.cpf = cpf
        self.contents = [] if contents is None else contents
        self.dyn_paths = [] if dyn_paths is None else dyn_paths

    def __str__(self):
        return str(self.cpf)


class ModelFileSystem:
    """
    Prefixed path operations.
    """

    def __init__(self, root=None):
        if root:
            self.root = pathlib.Path(root)
        else:
            self.root = pathlib.Path(tempfile.mkdtemp())

    def add(self, path, contents, strip=None):
        """
        Adds a new entry to the model filesystem.

        Parameters:
            path (str): relative path to file
            contents (str): new file contents
        """

        if strip:
            # Take the path but remove the root (parser.vdb)
            # e.g. /var/db/pkg/dev-perl/XML-Parser -> dev-perl/XML-Parser
            # and prefix with the temporary directory location
            # e.g. dev-perl/XML-Parser -> /tmp/tmp4nwthhg2/dev-perl/XML-Parser
            original_path = str(pathlib.Path(path)).replace(
                str(strip).rstrip("/"), str(self.root), 1
            )
            new_path = pathlib.Path(original_path)

            # Quick sanity check!
            if not str(new_path).startswith(str(self.root)):
                raise RuntimeError(
                    "Trying to write with non-prefixed path: {0}!".format(str(new_path))
                )

            # Create the parts above the files we're creating (e.g. NEEDED)
            # e.g. For dev-perl/XML-Parser/NEEDED, create two nested directories
            #      dev-perl/ and dev-perl/XML-Parser
            pathlib.Path(new_path.parent).mkdir(parents=True, exist_ok=True)
        else:
            new_path = pathlib.Path(path)

        new_path.write_text(contents)


def find_corrupt_pkgs(vdb_path, verbose=True):
    broken_packages = []

    # Generate a list of paths to check in the VDB
    path = pathlib.Path(vdb_path)
    vdb_dirs = path.glob("*/*")

    for full_path in vdb_dirs:
        # ${CATEGORY}/${PF}
        # e.g. net-misc/openssh-8.6_p1-r2
        cpf = full_path.relative_to(vdb_path)

        if "virtual/" in str(cpf) or "acct-" in str(cpf):
            continue

        # If they have a PROVIDES entry, skip.
        # They're not affected by the bug we're checking for.
        if (full_path / "PROVIDES").exists():
            if verbose:
                print("Skipping {0}".format(full_path))
            continue

        if "-MERGING-" in cpf.name:
            continue

        if ".portage_lockfile" in full_path.parts[-1]:
            continue

        # They have a PROVIDES entry.
        # Let's check if it installs any .sos
        # in CONTENTS.
        contents = full_path / "CONTENTS"
        if not contents.exists():
            print("!!! {0} has no CONTENTS file!".format(cpf))
            sys.exit(1)

        contents_lines = contents.read_text().split("\n")

        broken_package = None
        for line in contents_lines:
            if not line:
                continue

            installed_type, residue = line.split(" ", 1)
            # Discard anything other than 'obj'
            if installed_type != "obj":
                continue

            # Convert the residue into something useful
            installed_path = residue.split(" ")[0]

            match = re.match(".*\.so($|\..*)", installed_path)

            # If it's not .so-like, we'll still consider it if there's *bin* or *libexec*
            # in the path, to allow for packages which only install executables.
            if not match and (
                "bin" not in installed_path and "libexec" not in installed_path
            ):
                continue

            # Skip false positives where possible
            manpage = re.match("^/usr/(share|include)/", installed_path)
            if manpage:
                continue

            # TODO: We could batch this all at once and call file
            # on all the .so-ish paths installed by a package.
            file_exec = subprocess.run(["file", installed_path], stdout=subprocess.PIPE)
            file_exec_result = file_exec.stdout.decode("utf-8")

            if (
                "ELF" not in file_exec_result
                or "dynamically linked" not in file_exec_result
            ):
                if verbose:
                    print(
                        "Skipping {0}'s {1} because file says not a shared library".format(
                            str(cpf), installed_path
                        )
                    )
                continue

            # Don't spam about the same broken package repeatedly
            if not broken_package:
                broken_package = BrokenPackage(cpf)
                broken_packages.append(broken_package)
                if match:
                    # If they installed a .so-like file, then let's warn about it.
                    # This isn't important if it's just dynamically linked executables.
                    print(
                        "!!! {0} installed a dynamic library (or dyn. linked executable) with no PROVIDES!".format(
                            cpf
                        )
                    )

            broken_package.dyn_paths.append(installed_path)

            if broken_package:
                broken_package.contents.append(installed_path)

    return broken_packages


def fix_vdb(vdb_path, filesystem, pkg, verbose=True):
    """
    Creates PROVIDES, REQUIRES, NEEDED, and NEEDED.ELF.2 for a package in
    Portage's VDB.

    Parameters:
            pkg (BrokenPackage): An instance of a broken package
            pretend (bool): Pretend or modify the live filesystem

    Inspired by lib/portage/tests/util/dyn_libs/test_soname_deps.py
    """

    # Check whether any of the installed paths look .so-like
    any_so = (
        len([path for path in pkg.dyn_paths if re.match(".*\.so($|\..*)", path)]) > 0
    )

    if (vdb_path / pkg.cpf / "NEEDED").exists():
        # If we're installing any .so-like files, it's mildly
        # noteworthy if we have NEEDED but not PROVIDES.
        if any_so:
            if verbose:
                print(">>> NEEDED exists but no PROVIDES for {0}".format(pkg))

        # If NEEDED exists and we're just dynamically linked executables
        # (like sys-apps/sed), there's no point in carrying on either.
        # But so common that it's not even worth logging about.
        return

    corrected_vdb = {}

    print(">>> Fixing VDB for {0}".format(pkg))

    # We have missing PROVIDES, REQUIRES, NEEDED, NEEDED.ELF.2.
    #
    # 1) We create NEEDED, NEEDED.ELF.2.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = pathlib.Path(tmpdir)

        contents = " ".join(pkg.dyn_paths)

        subprocess.run(["recover-broken-vdb-scanelf.sh", tmpdir, contents])

        if not (tmpdir_path / "build-info" / "NEEDED").exists():
            # Not an interesting binary.
            print(">>> Nothing to fix for {0}, blank NEEDED".format(pkg))
            return

        for component in ["NEEDED", "NEEDED.ELF.2"]:
            corrected_vdb[component] = (
                tmpdir_path / "build-info" / component
            ).read_text()

    # 2) We now generate PROVIDES, REQUIRES
    soname_deps = SonameDepsProcessor("", "")
    for line in corrected_vdb["NEEDED.ELF.2"].split("\n"):
        if not line:
            continue

        needed = NeededEntry.parse(None, line)

        # We need a multilib category to satisfy
        # SonameDepsProcessor. It's not generated
        # by scanelf or similar, though. We
        # copy Portage's detection logic from
        # LinkageMapELF.rebuild() to determine
        # a value to use.
        if needed.multilib_category is None:
            needed.multilib_category = _approx_multilib_categories.get(
                needed.arch, needed.arch
            )

        soname_deps.add(needed)

    corrected_vdb["PROVIDES"] = soname_deps.provides
    corrected_vdb["REQUIRES"] = soname_deps.requires

    prefix = vdb_path + "/" + str(pkg)

    for entry in ["NEEDED", "NEEDED.ELF.2", "PROVIDES", "REQUIRES"]:
        if entry == "PROVIDES":
            # Did we install anything looking like a .so?
            # If so, we want to ensure PROVIDES isn't blank
            # (It's fine if we didn't install any .sos, because pkg w/ just dynamically linked executables
            # usually won't have a PROIVDES, unless FEATURES=splitdebug)
            if not corrected_vdb[entry]:
                if any_so:
                    raise RuntimeError(
                        "!!! {0} installed dynamic libraries(?) but no PROVIDES generated!".format(
                            pkg
                        )
                    )
                else:
                    # Don't try to write it out given no .sos installed
                    continue

        if verbose:
            print("File: {0}".format(entry.lstrip("/")))
            print("Value: {0}".format(corrected_vdb[entry]))

        filesystem.add(
            prefix + "/" + entry.lstrip("/"), corrected_vdb[entry], strip=vdb_path
        )

    print(">>> Generated fixed VDB files for {0}".format(pkg))
    print()


def start():
    parser = argparse.ArgumentParser(
        description="Tool to analyse Portage's VDB "
        "and check for ELF-metadata corruption."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List contents of each file we want to write",
    )
    parser.add_argument(
        "--vdb", type=str, default="/var/db/pkg", help="Path to Portage's VDB"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Location to write fixed VDB files to (default is a temporary directory)",
    )
    args = parser.parse_args()

    corrupt_pkgs = find_corrupt_pkgs(args.vdb, args.verbose)
    filesystem = ModelFileSystem(args.output)

    print()
    print(">> Writing to output directory: {0}".format(filesystem.root))

    for package in corrupt_pkgs:
        fix_vdb(args.vdb, filesystem, package, args.verbose)

    print(">>> Written to output directory: {0}".format(filesystem.root))


if __name__ == "__main__":
    start()
