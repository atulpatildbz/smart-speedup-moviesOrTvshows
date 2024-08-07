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
from moviepy.editor import VideoFileClip
import timeit
import logging
from datetime import datetime

def makeDirs():
    if(not args.continue_previous):
        if(os.path.exists(sped)):
            mainCleanup()
        os.mkdir(sped)
        os.mkdir(splittedInital)

def timeToSecs(t):
    return (t.hours * 60*60) + (t.minutes*60) + t.seconds + (t.milliseconds/1000)

def _endtime_to_end_sub(seconds): 
    millis = str(seconds-int(seconds))[2:5]
    millis_start = str((seconds-0.5)-int((seconds-0.5)))[2:5]
    seconds = seconds % (24 * 3600) 
    seconds_start = (seconds-0.5) % (24 * 3600) 
    hour = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return "\n\n%02d:%02d:%02d,%s --> %02d:%02d:%02d,%s\n.\n" % (hour, minutes, seconds_start, millis_start, hour, minutes, seconds, millis)

def slowerSplit(startTime, endTime, targetname):
    clip = VideoFileClip(filename).subclip(startTime, endTime)
    temp_audio = rawFile + '_temp-audio.m4a'
    try:
        clip.write_videofile(targetname, codec="libx264", temp_audiofile=temp_audio, remove_temp=True, audio_codec='aac')
    except:
        try:
            clip.write_videofile(targetname, codec="libx264", audio=False)
        except:
            pass

def makeSplitCommand(startTime, endTime, dors, namePrefix):
    if(args.continue_previous and os.path.exists(splittedInital+'/'+namePrefix+'_'+dors+'.mp4')):
        #check in the caller if this method returns false, skip the subprocess.call
        return False
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
    logging.info("Starting mainSplitWithOffset")
    if(not args.continue_previous or not os.path.exists(splitOffset)):
        logging.info(f"Creating directory: {splitOffset}")
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
        logging.debug(f"Subtitle {idx}: start={sub.start}, end={sub.end}, lastEnd={lastEnd}, diff={diff}")
        #if next subtitle is apart by just 1 sec, ignore the gap
        if((diff.seconds==0 and idx!=0) or (diff < pysrt.srttime.SubRipTime(0,0,0,0))):
            listOfTimes[-1][1] = sub.end
            logging.debug(f"Updated last end time to {sub.end}")
            continue
        listOfTimes.append([sub.start,sub.end])
        logging.debug(f"Appended subtitle times: start={sub.start}, end={sub.end}")

    i=0
    isInit = True
    for idx, t in enumerate(listOfTimes):
        logging.info(datetime.now().strftime("%b %d,%Y %H:%M:%S")+' Splitting progress: '+str(idx)+'/'+str(len(listOfTimes)))
        startSecs = timeToSecs(t[0])
        endSecs = timeToSecs(t[1])
        logging.debug(f"Processing time range: startSecs={startSecs}, endSecs={endSecs}, offset={offset}")
        if(startSecs>=offset):
            #split using command
            isInit = False
            logging.debug("Offset reached, switching to non-initial split")

        #for the gaps
        if(idx==0):
            if(isInit):
                command = makeSplitCommand(pysrt.srttime.SubRipTime(0,0,0,0),t[0],'s', "{0:0=5d}".format(i))
                logging.debug(f"Initial gap split command: {command}")
                if(command):
                    subprocess.call(command, shell=True)
            else:
                targetName = splitOffset+"/"+"{0:0=5d}".format(i)+'_s.mp4'
                logging.debug(f"Initial gap target name: {targetName}")
                if(not args.continue_previous or not os.path.exists(targetName)):
                    if(args.use_slower_split):
                        logging.debug("Using slower split for initial gap")
                        slowerSplit(timeToSecs(pysrt.srttime.SubRipTime(0,0,0,0)),startSecs, targetName)
                    else:
                        logging.debug("Using ffmpeg_extract_subclip for initial gap")
                        ffmpeg_extract_subclip(filename, timeToSecs(pysrt.srttime.SubRipTime(0,0,0,0)), startSecs, targetname=targetName)
        else:
            targetName = splitOffset+"/"+"{0:0=5d}".format(i)+'_s.mp4'
            logging.debug(f"Gap target name: {targetName}")
            if(not args.continue_previous or not os.path.exists(targetName)):
                if(isInit):
                    command = makeSplitCommand(listOfTimes[idx-1][1],t[0],'s', "{0:0=5d}".format(i))
                    logging.debug(f"Gap split command: {command}")
                    subprocess.call(command, shell=True)
                else:
                    if(args.use_slower_split):
                        logging.debug("Using slower split for gap")
                        slowerSplit(timeToSecs(listOfTimes[idx-1][1]) - offset, startSecs, targetName)
                    else:
                        logging.debug("Using ffmpeg_extract_subclip for gap")
                        ffmpeg_extract_subclip(filename, timeToSecs(listOfTimes[idx-1][1]) - offset, startSecs, targetname=targetName)
        i=i+1
        #for the dialogs
        if(isInit):
            command = makeSplitCommand(t[0],t[1],'d', "{0:0=5d}".format(i))
            logging.debug(f"Dialog split command: {command}")
            if(command):
                subprocess.call(command, shell=True)
        else:
            targetName = splitOffset+"/"+"{0:0=5d}".format(i)+'_d.mp4'
            logging.debug(f"Dialog target name: {targetName}")
            if(not args.continue_previous or not os.path.exists(targetName)):
                if(args.use_slower_split):
                    logging.debug("Using slower split for dialog")
                    slowerSplit(startSecs - offset, endSecs, targetName)
                else:
                    logging.debug("Using ffmpeg_extract_subclip for dialog")
                    ffmpeg_extract_subclip(filename, startSecs - offset, endSecs, targetname=targetName)
        i=i+1
    logging.info("Completed mainSplitWithOffset")

def mainSpeedUp(offset):
    #TODO: handle initsplit from different folder too
    listOfSpliWithoutOffset = os.listdir('./'+splittedInital)
    for file in listOfSpliWithoutOffset:
        if(args.continue_previous and os.path.exists(sped+'/'+file)):
            continue
        command = makeSpeedCommand(splittedInital+'/'+file, sped+'/'+file, dspeed, sspeed, 0)
        subprocess.call(command, shell=True)
    listOffsetFiles = os.listdir('./'+splitOffset)
    size = len(listOffsetFiles)
    i=0
    for file in listOffsetFiles:
        logging.info(datetime.now().strftime("%b %d,%Y %H:%M:%S")+'Speedup progress(in percent): '+ str(i/size))
        command = makeSpeedCommand(splitOffset+'/'+file, sped+'/'+file, dspeed, sspeed, offset)
        if(not args.continue_previous or not os.path.exists(sped+'/'+file)):
            subprocess.call(command, shell=True)
        i=i+1   #this isn't the right way to do this but i'm tired

def mainConcat():
    listOffsetFiles = sorted(os.listdir('./'+sped))
    f = open('mylist.txt', 'w+')
    for idx, file in enumerate(listOffsetFiles):
        f.write('file \''+sped+'/'+file+'\'\n')
        logging.info(datetime.now().strftime("%b %d,%Y %H:%M:%S")+'Concatenation progress(percent): '+str(idx/len(listOffsetFiles)))
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
    if os.path.exists('subs.srt'):
        os.remove('subs.srt')
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
    if os.path.exists(filename) and args.burn_subtitles:
        try:
            os.remove(filename)
        except:
            logging.error(f"{datetime.now().strftime('%b %d,%Y %H:%M:%S')} Error: failed to remove {filename} you'll have to remove it manually")
            pass
    #if os.path.exists(outputFileName):
    #    os.remove(outputFileName)

def mainBurnSubtitles():
    if(args.continue_previous and os.path.exists(filename)):
        return
    command = 'ffmpeg -i '+rawFile+' -vcodec libx264 -crf 27 -preset ultrafast -c:a aac -vf subtitles='+srtFile+' '+filename
    subprocess.call(command, shell=True)

def extractSrtFromMkv():
    command = 'ffmpeg -i '+rawFile+' -map 0:s:0 subs.srt'
    subprocess.call(command, shell=True)

start_time = timeit.default_timer()

parser = argparse.ArgumentParser(description='Modifies a video file to play at different speeds when there is sound vs. silence.')
parser.add_argument('-i','--input_file', type=str,  help='the video file you want modified')
parser.add_argument('-s','--subtitle_file', type=str,  help='the subtitle file to be process on')
parser.add_argument('-emkv','--extract_subs_mkv', action='store_true', help='extract subs from mkv')
parser.add_argument('-ds','--dialogue_speed', type=str,  help='the speed when someone is speaking')
parser.add_argument('-ss','--silence_speed', type=str,  help='the speed when theres silence')
parser.add_argument('--offset', type=str,  help='optional offset to tweak the split')
parser.add_argument('-b','--burn_subtitles', action='store_true', help='the speed when theres silence')
parser.add_argument('--use_slower_split', action='store_true', help='use this option if the default split gives incorrect results')
parser.add_argument('--no_cleanup', action='store_true', help='do not run cleanup after completion')
parser.add_argument('--continue_previous', action='store_true', help='continue previously aborted operation')

args = parser.parse_args()

rawFile = args.input_file
if(" " in rawFile):
    rawFile = "\""+rawFile+"\""
#use this code if subs are soft burned in video
#i tried using if else logic for -s options but that didn't seem to work so for now. commenting and uncommenting is the only option

logging.basicConfig(filename=f"{rawFile}.log", level=logging.DEBUG)

if(args.extract_subs_mkv):
    extractSrtFromMkv()
    srtFile = 'subs.srt'
else:
    srtFile = args.subtitle_file

if(args.burn_subtitles):
    filename = rawFile + 'burned.mp4'
else:
    filename = rawFile
splitOffset = rawFile + 'splittedWithOffset'
splittedInital = rawFile + 'splitted'
outputFileName = rawFile + '_output.mp4'
sped = rawFile + 'sped'
dspeed = float(args.dialogue_speed)
sspeed = float(args.silence_speed)

if(args.offset):
    offset = int(args.offset)
else:
    offset = 10

if(args.use_slower_split):
    offset = 0

rawVideoFileClip = VideoFileClip(rawFile)
videoFileClip = None
# preprocessing. may differ case to case
with open (srtFile, 'r', encoding="utf8") as f:
    content = f.read()
content_new = re.sub('\d+\n[\d:, ->]+\n\[[\D]*\]\n\n', '', content)
content_new = re.sub('[^A-Za-z\n\d: ->?]', '', content_new)
content_new = content_new + _endtime_to_end_sub(rawVideoFileClip.duration)
with open(srtFile, 'w+') as f:
    f.write(content_new)

subs = pysrt.open(srtFile, encoding='iso-8859-1')

# mainCleanup()

if(args.burn_subtitles):
    mainBurnSubtitles()
makeDirs()
try:
    mainSplitWithOffset()
except Exception as e:
    logging.error(datetime.now().strftime("%b %d,%Y %H:%M:%S")+"EXCEPTION occured while splitting")
    logging.error(e)
    pass

if(os.path.exists(f"{rawFile}_temp-audio.m4a")):
    logging.info("temp audio file exists, attempting to burn it to last split file")
    lastFileName = os.listdir(f"./{splitOffset}")[-1]
    subprocess.call(f"ffmpeg -i ./{splitOffset}/{lastFileName} -i {rawFile}_temp-audio.m4a -c copy -map 0:v:0 -map 1:a:0 {lastFileName}", shell=True)
    subprocess.call(f"rm ./{splitOffset}/{lastFileName}", shell=True)
    subprocess.call(f"mv {lastFileName} ./{splitOffset}/{lastFileName}", shell=True)
else:
    logging.info("temp audio file doesn't exist. Moving to next step")

mainSpeedUp(offset)
mainConcat()

if(not args.no_cleanup):
    mainCleanup()
# mainSyncSubs()

elapsed = timeit.default_timer() - start_time
print(f"Completed! took {elapsed} amount of time")
logging.info(f"{datetime.now().strftime('%b %d,%Y %H:%M:%S')} Completed! took {elapsed} amount of time")
