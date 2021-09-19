#!/bin/bash
# Directory to dump NEEDED{,.ELF.2} into
output=${1}
# We rather inefficiently invoke scanelf repeatedly for each file for now
# to avoid having to extract the binpkg to a separate location
files=${@:2}

mkdir -p ${output}/build-info

# Nabbed from Portage's bin/misc-functions.sh (install_qa_check)
scanelf -yRBF '%a;%p;%S;%r;%n' ${files} | { while IFS= read -r l; do
        arch=${l%%;*}; l=${l#*;}
        obj="${l%%;*}"; l=${l#*;}
        soname=${l%%;*}; l=${l#*;}
        rpath=${l%%;*}; l=${l#*;}; [ "${rpath}" = "  -  " ] && rpath=""
        needed=${l%%;*}; l=${l#*;}

        # Infer implicit soname from basename (bug 715162).
        if [[ -z ${soname} && $(file "${obj}") == *"SB shared object"* ]]; then
                soname=${obj##*/}
        fi

        echo "${obj} ${needed}" >> "${output}"/build-info/NEEDED
        echo "${arch#EM_};${obj};${soname};${rpath};${needed}" >> "${output}"/build-info/NEEDED.ELF.2
done }
