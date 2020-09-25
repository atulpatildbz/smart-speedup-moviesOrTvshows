# /usr/bin/env python
#
# Author: Red5d
#
# Description: Extract and run OCR on subtitles from a PGS-format .sup file.
#
# Example Usage: python sup2srt.py bd_subtitles.sup bd_subtitles.srt
#
# Dependencies:
# - pytesseract
# - tqdm
# - pysrt
# - pgsreader and imagemaker modules from (https://github.com/SavSanta/pgsreader)
#

import sys, pytesseract
from pgsreader import PGSReader
from imagemaker import make_image

from pysrt import SubRipFile, SubRipItem, SubRipTime

from tqdm import tqdm

supFile = sys.argv[1]
pgs = PGSReader(supFile)

srtFile = ".".join(supFile.split('.')[:-1])+".srt"
with open('myfile.txt', 'w') as fp: 
    pass

srt = SubRipFile()

# get all DisplaySets that contain an image
print("Loading DisplaySets...")
allsets = [ds for ds in tqdm(pgs.iter_displaysets())]

print(f"Running OCR on {len(allsets)} DisplaySets and building SRT file...")
subText = ""
subStart = 0
subIndex = 0
for ds in tqdm(allsets):
    try:
        if ds.has_image:
            # get Palette Display Segment
            pds = ds.pds[0]
            # get Object Display Segment
            ods = ds.ods[0]

            img = make_image(ods, pds)
            subText = pytesseract.image_to_string(img, lang='eng',  config='-l eng --oem 1 --psm 7')
            subStart = ods.presentation_timestamp
        else:
            startTime = SubRipTime(milliseconds=int(subStart))
            endTime = SubRipTime(milliseconds=int(ds.end[0].presentation_timestamp))
            srt.append(SubRipItem(subIndex, startTime, endTime, subText))
            subIndex += 1
    except:
        pass

print(f"Done. SRT file saved as {srtFile}")
srt.save(srtFile, encoding='utf-8')