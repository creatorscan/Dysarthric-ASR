#!/bin/bash

utils=/mnt/matylda3/karafiat/IITM_English_ASR_Challenge/OriginalRecipe/asr/utils


# ---- clean also STM
input_file=NPTEL_IITM_SP_Text_temp


input_file=$1
echo $input_file
#input_file=temp2

outfile="puncnormtxt"

cat $input_file|awk '{
for(i=2;i<=NF;i++){
  if ($i == "!SIL") $i = ""
  if ($i == "[NOISE]") $i = ""
  if ($i == "[VOCALIZED_NOISE]") $i = ""
  if ($i == "[{COUGH}]") $i = ""
  if ($i == "[unk]") $i = ""

   sub("[.,:!;?_\"]+$","",$i)
  sub("^[.,:!;?_\"]+","",$i)

  if($i ~ /^[0-9]+$/) {
    comm="~karafiat/bin/Number2String " $i;
      comm | getline expans; close(comm);
      $i=toupper(expans);
   }
}
print 
}'> $outfile


#which python 
#python /mnt/matylda3/vydana/kaldi/egs/Pyscripts/N2W.py "$outfile"



