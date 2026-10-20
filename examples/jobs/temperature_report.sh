#!/usr/bin/env bash
export LC_ALL=C
mkdir -p results
awk -F, '
    NR > 1 {
        count++
        sum += $2
        if (count == 1 || $2 < minimum) minimum = $2
        if (count == 1 || $2 > maximum) maximum = $2
    }
    END {
        if (count == 0) {
            print "No accepted sensor readings" > "/dev/stderr"
            exit 1
        }
        print "Temperature report"
        printf "Accepted readings: %d\n", count
        printf "Mean temperature: %.1f C\n", sum / count
        printf "Range: %.1f to %.1f C\n", minimum, maximum
    }
' inputs/clean.csv > results/report.txt
cat results/report.txt
