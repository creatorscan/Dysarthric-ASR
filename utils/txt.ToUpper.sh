#!/bin/bash
for i in $*; do
    case "$1" in
        -locale)
            export LANG=$2
	    export LC_ALL=$LANG
            shift
            shift
            ;;
        *)
            break
            ;;
    esac
done

IN=$1
OU=$2

if [ "$IN" == "-" ];               then IN=/dev/stdin; fi
if [ "$OU" == "-" ] || [ -z $OU ]; then OU=/dev/stdout;fi

awk '{print toupper($0)}' $IN > $OU