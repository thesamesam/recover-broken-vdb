#!/bin/bash
# Identify packages in the VDB that provide executables or .so files,
# but are missing an expected PROVIDES or NEEDED* file.

die()
{
  echo "$@" >&2
  exit 1
}

# Sanity check.
declare -a pkgs || die "Declaring array failed, old bash? Cannot continue."

# Let stderr bleed through, if any.
vdb_path=$(portageq vdb_path)
test -n "${vdb_path}" || die "Could not determine vdb_path. Cannot continue."

cd "${vdb_path}" || die "Could not chdir vdb_path (${vdb_path}). Cannot continue."

echo "# Checking installed packages for inconsistent VDB..."
for A in */*/CONTENTS ; do
    CPV=$(echo "${A}" | cut -d/ -f1,2)

    # Iterate over all potential shared libs or executables they install
    for O in $(sed -n -E 's%^obj (/.*(\.so( |\.[^ ]+)|bin/[^ ]+)).*%\1%p' ${A} | sed 's/ $//') ; do
        # Check if the file is really a shared object or executable
        F=$(file -b "${O}" 2>/dev/null)
        test -n "${F}" || die "Could not run 'file' on '${O}'. Cannot continue."

        # If it is an executable, check that we have NEEDED* metadata in the VDB
        if echo "${F}" | egrep -q "ELF .*executable.*dynamically linked" ; then
            if [ ! -f "${CPV}/NEEDED" -o ! -f "${CPV}/NEEDED.ELF.2" ] ; then
                # Remember this package with full version suitable for re-emerging
                pkgs+=("=${CPV}")
                # We know this package is broken, move on to the next
                break
            fi
        # If it is a shared library, check that we have NEEDED* and PROVIDES in the VDB
        elif echo "${F}" | egrep -q "ELF .*shared object" ; then
            if [ ! -f "${CPV}/PROVIDES" -a ! -f "${CPV}/NEEDED" -a ! -f "${CPV}/NEEDED.ELF.2" ] ; then
                # Remember this package with full version suitable for re-emerging
                pkgs+=("=${CPV}")
            fi
            # We have checked this package thoroughly so we can move on
            break
        fi
    done
done

# Output the affected packages, if any
if (( ${#pkgs[@]} )) ; then printf "%s\n" "${pkgs[@]}" ; fi
