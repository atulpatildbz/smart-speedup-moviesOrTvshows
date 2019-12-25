# Smart Fast Forward for Movies and TV shows

This program provides a way to convert any movie or tv show episode and fast forward it such that parts where noone speaks is sped up, and part where someone is speaking is not.
This allows us to watch a movie quickly and without missing out any dialogues.

Some prerequisites needed:
  - ffmpeg: this is an open source video software to work with video/audio
  - pysrt: this python library is required to work with srt files
  - moviepy: python library for ffmpeg


### Installation

**ffmpeg**
Full installation guide:
https://github.com/adaptlearning/adapt_authoring/wiki/Installing-FFmpeg

**pysrt**
```sh
$ pip install pysrt
```

**moviepy**
```sh
$ pip install moviepy
```

### Usage

Run the following command to get help:
```sh
$ python splitspeedconcatV2.py --help
  -h, --help            show this help message and exit
  -i INPUT_FILE, --input_file INPUT_FILE
                        the video file you want modified
  -s SUBTITLE_FILE, --subtitle_file SUBTITLE_FILE
                        the subtitle file to be process on
  -ds DIALOGUE_SPEED, --dialogue_speed DIALOGUE_SPEED
                        the speed when someone is speaking
  -ss SILENCE_SPEED, --silence_speed SILENCE_SPEED
                        the speed when there is silence
  -b BURN_SUBTITLES, --burn_subtitles BURN_SUBTITLES
                        the speed when theres silence
  --use_slower_split USE_SLOWER_SPLIT
                        use this option if the default split gives incorrect results
```
