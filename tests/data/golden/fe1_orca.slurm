#!/bin/bash
#SBATCH --partition=compute
#SBATCH --nodes=1
# usage: sbatch -J <stem> -n <nprocs> -c 1 --mem=<GB>G -t <HH:MM:SS> fe1_orca.slurm <stem>
# Runs <stem>.inp (and any <stem>*.xyz it reads) from the submit dir in node
# scratch with ORCA 6.1.0 and copies results back on exit (also on failure).
# %pal nprocs in the .inp must equal -n (OpenMPI needs one Slurm task per rank). Fe(I) project only.
set -u
export USER=${USER:-$(id -un)}
STEM=$1
SIF=/images/orca.sif
SUBMIT=$SLURM_SUBMIT_DIR
WORK=/scratch/$USER/fe1_orca_${STEM}_${SLURM_JOB_ID}
mkdir -p "$WORK" && cd "$WORK" || exit 2
cp "$SUBMIT/$STEM.inp" .
for f in "$SUBMIT"/*.xyz; do [ -f "$f" ] && cp "$f" .; done
copyback() {
  for f in "$STEM".out "$STEM".xyz "$STEM"_trj.xyz "$STEM".property.txt \
           "$STEM".engrad "$STEM".hess "$STEM".gbw "$STEM".err; do
    [ -f "$f" ] && cp -f "$f" "$SUBMIT/"
  done
  cd / && rm -rf "$WORK"
}
trap copyback EXIT
echo "fe1_orca: job=$SLURM_JOB_ID stem=$STEM node=$(hostname) sif=$SIF"
cat > run.sh <<EOF
#!/bin/bash
\$ORCADIR/orca $STEM.inp > $STEM.out 2> $STEM.err
EOF
chmod +x run.sh
if command -v apptainer >/dev/null; then RT=apptainer; else RT=/usr/local/bin/singularity; fi
$RT run -B "$WORK" --pwd "$WORK" "$SIF" ./run.sh
st=$?
grep -q "ORCA TERMINATED NORMALLY" "$STEM.out" && echo "fe1_orca: normal" || echo "fe1_orca: FAILED (rc=$st)"
