# -*- coding: utf-8 -*-
"""
Created on Sun Apr 14 22:16:38 2019

@author: Atul
"""

'''
This is the new optimized version of splitspeedconcat.
Tyring to achieve faster speed by first splitting videos with copy vcodec and 
with an offset of 10sec. This will overcome limitation of missing starting frames.
Then we will feed these videos to speed up function and then finally concatenate
'''

import subprocess
import pysrt
import os
import shutil
import copy
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
import argparse
import re


parser = argparse.ArgumentParser(description='Modifies a video file to play at different speeds when there is sound vs. silence.')
parser.add_argument('-i','--input_file', type=str,  help='the video file you want modified')
parser.add_argument('-s','--subtitle_file', type=str,  help='the subtitle file to be process on')
parser.add_argument('-ds','--dialogue_speed', type=str,  help='the speed when someone is speaking')
parser.add_argument('-ss','--silence_speed', type=str,  help='the speed when theres silence')

args = parser.parse_args()


rawFile = args.input_file
filename = 'burned.mp4'
srtFile = args.subtitle_file
splitOffset = 'splittedWithOffset'
splittedInital = 'splitted'
outputFileName = 'output.mp4'
sped = 'sped'
offset = 10
dspeed = float(args.dialogue_speed)
sspeed = float(args.silence_speed)

subs = pysrt.open(srtFile, encoding='iso-8859-1')

def makeDirs():
    os.mkdir(sped)
    os.mkdir(splittedInital)

def timeToSecs(t):
    return (t.hours * 60*60) + (t.minutes*60) + t.seconds + (t.milliseconds/1000)

def makeSplitCommand(startTime, endTime, dors, namePrefix):
    command = 'ffmpeg'
    command = command + ' -i '+filename
    command = command + ' -vcodec libx264 -crf 27 -preset ultrafast -c:a copy'
    command = command +' -ss '+str(startTime.hours)+':'+str(startTime.minutes)+':'+str(startTime.seconds)+'.'+str(startTime.milliseconds)
    diff = endTime - startTime
    secs = (diff.minutes * 60) + diff.seconds
    command = command +' -t '+str(secs)+'.'+str(diff.milliseconds)
    command = command + ' '+splittedInital+'/'+namePrefix+'_'+dors+'.mp4'
    return command

def makeSpeedCommand(inFile, outFile, dspeed, sspeed, offset):
    #vspeed works in this way 1/x where x is the value below
    # vspeed = '0.8'
    #aspeed works in this way 1 * x where x is the value below
    #here, x has to be below 2. therefore in order to achieve higher speed we have to do multiplication
    #e.g. 2x*2x = 4x
    
    #25% works perfectly since there aren't more than 2 decimal places
    # aspeed = 'atempo=1.25'
    if("_s.mp4" in inFile):
        #for silent parts, increase speed
        speed = sspeed
    else:
        speed = dspeed
    
    vspeed = str(1/speed)
    offset = offset/speed
    aspeed = ''
    while(speed>2):
        aspeed = aspeed + 'atempo=2.0,'
        speed = speed/2
    aspeed = aspeed + 'atempo='+str(float(speed))

    command = 'ffmpeg -i '+inFile+' -ss '+str(offset)+' -vcodec libx264 -crf 27 -preset ultrafast -filter:v "setpts='+vspeed+'*PTS" -filter:a "'+aspeed+'" -max_muxing_queue_size 1024 '+outFile
    return command

def mainSplitWithOffset():
    os.mkdir(splitOffset)
    listOfTimes = []

    i=0
    for idx, sub in enumerate(subs):
        i=i+1
        if(idx==0):
            lastEnd = pysrt.srttime.SubRipTime(0,0,0,0)
        else:
            lastEnd = listOfTimes[-1][1]
        diff = sub.start - lastEnd
        #if next subtitle is apart by just 1 sec, ignore the gap
        if(diff.seconds==0 and idx!=0):
            listOfTimes[-1][1] = sub.end
            continue
        listOfTimes.append([sub.start,sub.end])

    i=0
    isInit = True
    for idx, t in enumerate(listOfTimes):
        startSecs = timeToSecs(t[0])
        endSecs = timeToSecs(t[1])
        if(startSecs>=10):
            #split using command
            isInit = False

        #fot the gaps
        if(idx==0):
            if(isInit):
                command = makeSplitCommand(pysrt.srttime.SubRipTime(0,0,0,0),t[0],'s', "{0:0=5d}".format(i))
                subprocess.call(command, shell=True)
            else:
                ffmpeg_extract_subclip(filename, timeToSecs(pysrt.srttime.SubRipTime(0,0,0,0)), startSecs, targetname=splitOffset+"/"+"{0:0=5d}".format(i)+'_s.mp4')
        else:
            if(isInit):
                command = makeSplitCommand(listOfTimes[idx-1][1],t[0],'s', "{0:0=5d}".format(i))
                subprocess.call(command, shell=True)
            else:
                ffmpeg_extract_subclip(filename, timeToSecs(listOfTimes[idx-1][1]) - offset, startSecs, targetname=splitOffset+"/"+"{0:0=5d}".format(i)+'_s.mp4')
        i=i+1
        #for the dialogs
        if(isInit):
            command = makeSplitCommand(t[0],t[1],'d', "{0:0=5d}".format(i))
            subprocess.call(command, shell=True)
        else:
            ffmpeg_extract_subclip(filename, startSecs - offset, endSecs, targetname=splitOffset+"/"+"{0:0=5d}".format(i)+'_d.mp4')
        i=i+1

def mainSpeedUp(offset):
    #TODO: handle initsplit from different folder too
    listOfSpliWithoutOffset = os.listdir('./'+splittedInital)
    for file in listOfSpliWithoutOffset:
        command = makeSpeedCommand(splittedInital+'/'+file, sped+'/'+file, dspeed, sspeed, 0)
        subprocess.call(command, shell=True)
    listOffsetFiles = os.listdir('./'+splitOffset)
    for file in listOffsetFiles:
        command = makeSpeedCommand(splitOffset+'/'+file, sped+'/'+file, dspeed, sspeed, offset)
        subprocess.call(command, shell=True)

def mainConcat():
    listOffsetFiles = os.listdir('./'+sped)
    f = open('mylist.txt', 'w+')
    for file in listOffsetFiles:
        f.write('file \''+sped+'/'+file+'\'\n')
    f.close()
    command = 'ffmpeg -f concat -i mylist.txt -c copy '+outputFileName
    subprocess.call(command, shell=True)

def mainSyncSubs():
    dupSubs = copy.deepcopy(subs)
    for idx, sub in enumerate(subs):
        gapSpeed = sspeed
        speakSpeed = dspeed
        startTime = sub.start
        endTime = sub.end
        if(startTime.hours != subs[idx-1].end.hours):
            startTime.minutes = startTime.minutes + 60
        if(endTime.hours != startTime.hours):
            endTime.minutes = endTime.minutes + 60
        if(idx==0):
            diffGap = startTime
            prevEnd = pysrt.srttime.SubRipTime(0,0,0,0)
        else:
            diffGap = startTime - subs[idx-1].end
            prevEnd = dupSubs[idx-1].end
        
        millisDiffGap = (diffGap.seconds * 1000) + diffGap.milliseconds
        #since we aren't speeding up if gap between subtitles is less than 1 sec
        if(diffGap.seconds==0):
            correctedGap = int(millisDiffGap / speakSpeed)
        else:
            correctedGap = int(millisDiffGap / gapSpeed)
        correctedGapTime = pysrt.srttime.SubRipTime(0,0,correctedGap//1000,correctedGap%1000)
        dupSubs[idx].start = prevEnd + correctedGapTime  +pysrt.srttime.SubRipTime(0,0,0,110)
        

        diffDuration = endTime - startTime
        millisDiffDuration = (diffDuration.seconds * 1000) + diffDuration.milliseconds
        correctedDiffDuration = int(millisDiffDuration/speakSpeed)
        correctedDiffDurationTime = pysrt.srttime.SubRipTime(0,0,correctedDiffDuration//1000,correctedDiffDuration%1000)
        dupSubs[idx].end = dupSubs[idx].start + correctedDiffDurationTime

    dupSubs.save('adjusted.srt', encoding='utf-8')

def mainCleanup():
    if os.path.exists(filename):
        os.remove(filename)
    if os.path.exists('adjusted.srt'):
        os.remove('adjusted.srt')
    if os.path.exists('mylist.txt'):
        os.remove('mylist.txt')
    if os.path.exists(sped):
        shutil.rmtree('./'+sped)
    if os.path.exists(splittedInital):
        shutil.rmtree('./'+splittedInital)
    if os.path.exists(splitOffset):
        shutil.rmtree('./'+splitOffset)
    #if os.path.exists(outputFileName):
    #    os.remove(outputFileName)

def mainBurnSubtitles():
    command = 'ffmpeg -i '+rawFile+' -vcodec libx264 -crf 27 -preset ultrafast -c:a copy -vf subtitles='+srtFile+' '+filename
    subprocess.call(command, shell=True)

mainCleanup()

mainBurnSubtitles()
makeDirs()
mainSplitWithOffset()
mainSpeedUp(offset)
mainConcat()

mainCleanup()
# mainSyncSubs()
