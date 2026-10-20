#!/usr/bin/env bash
# Keep the header and readings that passed the sensor's quality check.
export LC_ALL=C
mkdir -p results
awk -F, 'NR == 1 || $3 == "ok"' inputs/readings.csv > results/clean.csv
printf 'Cleaned readings written to results/clean.csv\n'
