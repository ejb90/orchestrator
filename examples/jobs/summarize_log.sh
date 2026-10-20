#!/usr/bin/env bash
export LC_ALL=C
mkdir -p results
awk '
    NF { entries++; counts[$2]++ }
    END {
        print "Application log summary"
        printf "Entries: %d\n", entries
        printf "Warnings: %d\n", counts["WARN"]
        printf "Errors: %d\n", counts["ERROR"]
    }
' inputs/application.log > results/summary.txt
cat results/summary.txt
