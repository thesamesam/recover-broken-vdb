#!/bin/bash
# Identify packages that provide .so files but are missing a PROVIDES file

pkgs=$(cd /var/db/pkg && for A in */*/CONTENTS ; do
    # Iterate over all .sos they install
    for O in $(sed -n -E 's%^obj (/[^ ]+\.so( |\.[^ ]+)).*%\1%p' $A | sed 's/ $//') ; do
        SHARED=$(file "$O" | egrep 'shared object')
        # Check if the file is really a shared object
        if [ -n "$SHARED" ]; then
            T=$(echo "$A" | cut -d/ -f1,2)

            # If it is, complain if we are missing the expected ELF metadata in the VDB
            if [ ! -f "${T}/PROVIDES" -o ! -f "${T}/NEEDED" -o ! -f "${T}/NEEDED.ELF.2" ]; then
                echo "${T}"
            fi

            # We checked this package already so we can bail out
            break
        fi
    done
done)

# Give exact versions suitable for emerge in output
echo "${pkgs}" | sed -e 's:^:=:g'
