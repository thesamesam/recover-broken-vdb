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


class Package:
    """
    Represents useful information about a package in the VDB.

    Attributes
    ----------
    cpf: str
        ${CATEGORY}/${PF} - category/revision
    contents : list
        list of the installed files
    dyn_paths: list
        list of the installed dynamic libraries to check for integrity
    """

    def __init__(self, cpf, vdb_path, contents=None, dyn_paths=None, broken=False):
        self.cpf = str(cpf)

        # TODO: Could shift around ModelFileSystem so we have a VDB object?
        self.vdb_path = vdb_path

        self.contents = [] if contents is None else contents
        self.dyn_paths = [] if dyn_paths is None else dyn_paths

        self.broken = broken

        # We need something to populate this
        # Right now, we do it based on contents in find_corrupt_pkgs
        self.installs_any_dyn_executable = None
        self.installs_any_shared_libs = None

    def exists(self, key):
        return (pathlib.Path(self.vdb_path) / self.cpf / key).exists()

    def __str__(self):
        return self.cpf


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

        if contents:
            new_path.write_text(contents)
        else:
            raise ValueError(
                "Contents of new file (path={0}) cannot be blank!".format(path)
            )


def find_corrupt_pkgs(vdb_path, deep=True, verbose=True):
    broken_packages = []
    unexpected_case_found = False

    # Generate a list of paths to check in the VDB
    path = pathlib.Path(vdb_path)
    vdb_dirs = path.glob("*/*")

    for full_path in vdb_dirs:
        # ${CATEGORY}/${PF}
        # e.g. net-misc/openssh-8.6_p1-r2
        cpf = full_path.relative_to(vdb_path)
        package = Package(cpf, vdb_path)

        if "virtual/" in package.cpf or "acct-" in package.cpf:
            continue

        # Be conservative for now given we don't yet have enough information to skip
        # with some more precision (like in -find-broken.sh)
        # (We used to skip more aggressively here with an 'or')
        if package.exists("PROVIDES") and package.exists("NEEDED"):
            if verbose:
                print(
                    ">>> Skipping {0} because PROVIDES and NEEDED exists".format(
                        package.cpf
                    )
                )
            continue

        if "-MERGING-" in package.cpf:
            continue

        if ".portage_lockfile" in full_path.parts[-1]:
            continue

        # They have a PROVIDES entry.
        # Let's check if it installs any .sos
        # in CONTENTS.
        contents = full_path / "CONTENTS"
        if not contents.exists():
            print("!!! {0} has no CONTENTS file!".format(package.cpf))
            sys.exit(1)

        # Don't populate package.contents yet, as we'd like to filter
        # out irrelevant paths first.
        contents = contents.read_text().split("\n")

        package.installs_any_shared_libs = False
        package.installs_any_dyn_executable = False

        for line in contents:
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
            if not match:
                # If --deep is given, examine these paths even if they're unlikely to be
                # critical.
                if (
                    not deep
                    and "bin" not in installed_path
                    and "libexec" not in installed_path
                ):
                    continue

            # Skip false positives where possible
            ignore_path = re.match("^/usr/(share|include)/", installed_path)
            if ignore_path:
                continue

            # TODO: We could batch this all at once and call file
            # on all the .so-ish paths installed by a package.
            file_exec = subprocess.run(
                ["file", "-b", installed_path], stdout=subprocess.PIPE
            )
            file_exec_result = file_exec.stdout.decode("utf-8")

            if (
                "ELF" not in file_exec_result
                or "dynamically linked" not in file_exec_result
            ):
                if verbose:
                    print(
                        "Skipping {0}'s {1} because file says not a shared library or dyn. linked executable".format(
                            package.cpf, installed_path
                        )
                    )
                continue

            if (
                "executable" in file_exec_result
                and not package.installs_any_dyn_executable
            ):
                package.installs_any_dyn_executable = True

            if (
                "shared object" in file_exec_result
                and not package.installs_any_shared_libs
            ):
                package.installs_any_shared_libs = True

            # Don't spam about the same broken package repeatedly
            if not package.broken:
                package.broken = True

                if match:
                    # If they installed a .so-like file, then let's warn about it.
                    # This isn't important if it's just dynamically linked executables.
                    print(
                        "!!! {0} installed a dynamic library (or dyn. linked executable) with no PROVIDES!".format(
                            package.cpf
                        )
                    )

            package.dyn_paths.append(installed_path)

            # We could do this for every package but it's a waste of memory.
            if package.broken:
                package.contents.append(installed_path)

        # Now that we've iterated over all the files installed by this package,
        # we can apply a bit more filtering.
        #
        # 1) If a package has both PROVIDES and NEEDED* (checked already)
        # => it is definitely safe
        #
        # NOTE: We've already checked for "PROVIDES _and_ NEEDED" at the beginning of the function

        # 2) If a package has zero executables or shared libs
        # => it probably has neither PROVIDES or NEEDED*, but in any case we don't care about it
        #
        if (
            not package.installs_any_dyn_executable
            and not package.installs_any_shared_libs
        ):
            if verbose:
                print(
                    ">>> Package {0} is fine: not installing any dyn. linked executables or shared libs".format(
                        package.cpf
                    )
                )
            continue

        # 3) If a package has NEEDED* but no PROVIDES
        # => it is safe only if it has zero shared libs
        #
        if package.exists("NEEDED") and not package.exists("PROVIDES"):
            if package.installs_any_shared_libs:
                if verbose:
                    print(
                        ">>> Package {0} is broken: NEEDED but no PROVIDES and we install shared libs".format(
                            package.cpf
                        )
                    )

                broken_packages.append(package)
            else:
                if verbose:
                    print(
                        ">>> Package {0} is fine: NEEDED exists but no PROVIDES and no shared libs".format(
                            package.cpf
                        )
                    )
        elif package.exists("PROVIDES") and not package.exists("NEEDED"):
            # 4) If a package has PROVIDES but no NEEDED*
            # => it is definitely broken (for some value of definitely)
            #
            if verbose:
                print(
                    ">>> Package {0} is broken: PROVIDES but no NEEDED".format(
                        package.cpf
                    )
                )

            broken_packages.append(package)
        elif (
            not package.exists("PROVIDES")
            and not package.exists("NEEDED")
            and not package.exists("REQUIRES")
        ):
            # 5) None of the important files and we're installing at least one of
            # shared libs or dynamically linked executables.
            # => definitely broken!
            #
            if verbose:
                print(
                    ">>> Package {0} is broken: no PROVIDES, NEEDED, or REQUIRES".format(
                        package.cpf
                    )
                )

            broken_packages.append(package)
        else:
            # We've hit an unexpected case
            print(
                "!!! Unexpected case, please report this as a bug with the following info:"
            )
            print("!!!  cpf: {0}".format(package))

            for key in ["NEEDED", "PROVIDES", "REQUIRES"]:
                print("!!!  {0} exists: {1}".format(key, package.exists(key)))

            print(
                "!!!  installs_any_dyn_executable: {0}".format(
                    package.installs_any_dyn_executable
                )
            )

            print(
                "!!!  installs_any_shared_libs: {0}".format(
                    package.installs_any_shared_libs
                )
            )
            print("!!!  deep: {0}".format(deep))

            if not unexpected_case_found:
                unexpected_case_found = True

    return broken_packages, unexpected_case_found


def fix_vdb(vdb_path, filesystem, package, verbose=True):
    """
    Creates PROVIDES, REQUIRES, NEEDED, and NEEDED.ELF.2 for a package in
    Portage's VDB.

    Parameters:
            pkg (Package): An instance of a (broken) package
            pretend (bool): Pretend or modify the live filesystem

    Inspired by lib/portage/tests/util/dyn_libs/test_soname_deps.py
    """

    corrected_vdb = {}

    print(">>> Fixing VDB for {0}".format(package))

    # We have missing PROVIDES, REQUIRES, NEEDED, NEEDED.ELF.2.
    #
    # 1) We create NEEDED, NEEDED.ELF.2.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = pathlib.Path(tmpdir)

        contents = " ".join(package.dyn_paths)

        subprocess.run(["recover-broken-vdb-scanelf.sh", tmpdir, contents])

        if not (tmpdir_path / "build-info" / "NEEDED").exists():
            # Not an interesting binary.
            print(">>> Nothing to fix for {0}, blank NEEDED".format(package))
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

    prefix = vdb_path + "/" + str(package)

    for entry in ["NEEDED", "NEEDED.ELF.2", "PROVIDES", "REQUIRES"]:
        if entry == "PROVIDES":
            # Did we install anything looking like a .so?
            # If so, we want to ensure PROVIDES isn't blank
            # (It's fine if we didn't install any .sos, because pkg w/ just dynamically linked executables
            # usually won't have a PROIVDES, unless FEATURES=splitdebug)
            if not corrected_vdb[entry]:
                if package.installs_any_shared_libs:
                    raise RuntimeError(
                        "!!! {0} installed dynamic libraries(?) but no PROVIDES generated!".format(
                            package
                        )
                    )
                else:
                    # Don't try to write it out given no .sos installed
                    continue

        if verbose:
            print("File: {0}".format(entry.lstrip("/")))
            print("Value: {0}".format(corrected_vdb[entry]))

        try:
            filesystem.add(
                prefix + "/" + entry.lstrip("/"), corrected_vdb[entry], strip=vdb_path
            )
        except ValueError:
            # Seems to happen if installed e.g. an executable-only package with an older
            # version of Portage.
            # https://bugs.gentoo.org/815493
            print(
                "??? Tried to write blank entry={0} for pkg={1}. Likely harmless. Skipping entry.".format(
                    entry, package
                )
            )

    print(">>> Generated fixed VDB files for {0}".format(package))
    print()


def start():
    parser = argparse.ArgumentParser(
        description="Tool to analyse Portage's VDB "
        "and check for ELF-metadata corruption."
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Rebuild VDB contents for non-critical packages (increases time taken); based on being in a path matching *bin* or *libexec*",
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

    print(">>> Scanning system. This may take a few minutes.")

    if args.deep:
        print("!!! Running with --deep may significantly increase runtime. Be patient!")

    corrupt_pkgs, unexpected_case_found = find_corrupt_pkgs(
        args.vdb, args.deep, args.verbose
    )

    if unexpected_case_found:
        print("!!! Aborting due to unexpected case(s) found")
        sys.exit(1)

    if corrupt_pkgs:
        filesystem = ModelFileSystem(args.output)

        print(">>> Found {0} packages to fix".format(len(corrupt_pkgs)))
        print(">>> Writing to output directory: {0}".format(filesystem.root))

        for package in corrupt_pkgs:
            fix_vdb(args.vdb, filesystem, package, args.verbose)

        print(">>> Written to output directory: {0}".format(filesystem.root))
    else:
        print(">>> No broken packages found!")


if __name__ == "__main__":
    start()
