#!/bin/bash
# Identify packages in the VDB that provide .so files but are missing an
# expected PROVIDES or NEEDED* file.

die()
{
  echo "$@" >&2
  exit 1
}

# Sanity check.
declare -a pkgs || die "Declaring array failed, old bash? Cannot continue."

# Let stderr bleed through, if any.
vdb_path=$(portageq vdb_path)
test -n "$vdb_path" || die "Could not determine vdb_path. Cannot continue."

cd "$vdb_path" || die "Could not chdir vdb_path ($vdb_path). Cannot continue."

for A in */*/CONTENTS ; do
    # Iterate over all .sos they install
    for O in $(sed -n -E 's%^obj (/[^ ]+\.so( |\.[^ ]+)).*%\1%p' $A | sed 's/ $//') ; do
        SHARED=$(file "$O" | egrep 'shared object')
        # Check if the file is really a shared object
        if [ -n "$SHARED" ]; then
            T=$(echo "$A" | cut -d/ -f1,2)

            # If it is, complain if we are missing the expected ELF metadata in the VDB
            if [ ! -f "${T}/PROVIDES" -o ! -f "${T}/NEEDED" -o ! -f "${T}/NEEDED.ELF.2" ]; then
                # Remember this package with full version suitable for re-emerging
                pkgs+=("=${T}")
            fi

            # We checked this package already so we can bail out
            break
        fi
    done
done

# Output the affected packages, if any
if (( ${#pkgs[@]} )) ; then printf "%s\n" "${pkgs[@]}" ; fi
