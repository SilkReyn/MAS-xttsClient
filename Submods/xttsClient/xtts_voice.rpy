init -10 python in xttsClient:
    import urllib2
    import json
    import re
    import Queue as queue  # not shipped with renpy?
    import threading
    import time
    
    from store import MASAudioData
    from store.mas_submod_utils import submod_log


    class SayFunctor(object):
        """Represents a call of renpy.say."""
        
        # This shall be the initial say function
        renpy_say = staticmethod(renpy.say)
        
        def __init__(self, args, kwargs):
            """
            IN:
                args - positional arguments to call say with
                kwargs - keyword arguments to call say with

            Example:
                f = SayFunctor((who, what) + args, {interact=True})
            """
            self.args = args
            self.kwargs = kwargs
        
        def __call__(self):
            """Calls renpy.say with arguments taken from this instance"""
            submod_log.debug("calling {0} with {1} and {2}".format(self.renpy_say, self.args, self.kwargs))
            SayFunctor.renpy_say(*self.args, **self.kwargs)  # Callable only from main thread and same context
            # Added in renpy 8.2.0..24012702 (Nov2023)
            #renpy.invoke_in_main_thread(SayFunctor.renpy_say, *self.args, **self.kwargs)


    RPY_SAY_FN = renpy.say
    FILENAMES = [
        "Submods/xttsClient/mod_assets/voice/speech1.wav",
        "Submods/xttsClient/mod_assets/voice/speech2.wav",
        "Submods/xttsClient/mod_assets/voice/speech3.wav"]
    HOST_URL = "http://localhost:8020/tts_to_file"
    SRC_URL = "http://localhost:8020/tts_to_audio/"
    REQ_HEAD = {
        'accept': "application/json",
        'Content-Type': "application/json"
    }
    ROOT_DIR = renpy.config.gamedir.replace("\\", "/") + "/"
    # Regex to remove laughter, fast repetition and text-tags
    FILTER = re.compile(r"\b[tTwW]?[aAeE]?(?:[hH][aei]){2,}\b[,!.]? ?|.*{fast}|{.*?}")
    VOICE_READY_EV = threading.Event()
    # When true blocks interaction until voice is available
    # Remove this switch when implemented without queue between threads
    PREF_AWAIT_VOICE = False


    def filterDialogue(text):
        # renpy.filter_text_tags was added in r6.99.13 T_T
        return FILTER.sub("", renpy.substitute(text)).replace("~", " ").replace("...", "; ").strip(' ,-;')

    def passDialogue(what, callback=None):  # only monika!
        if renpy.config.has_voice and not renpy.config.skipping:
            speech = filterDialogue(what)
            # Ignore non-verbal text; Must contain a syllable at least
            if (len(speech) > 1 and re.search(r"\w{2,}", speech)):
                if '{fast}' in what:
                    renpy.store.voice_sustain()
                    speech = '{sustain}' + speech
                if re.search(r"{nw=?\d*}\Z", what):
                    speech += '{complete}'
                VOICE_READY_EV.clear()
                tts.send((speech, callback))
                # Pause seem to proceed current context
                #while not VOICE_READY_EV.isSet():
                #    renpy.pause(1)
                if PREF_AWAIT_VOICE:
                    VOICE_READY_EV.wait()
                return True
        return False

    def sayWithXtts(who, what, *args, **kwargs):
        if (renpy.store.m_name == str(who) and
            renpy.game.preferences.volumes['sfx'] > 0 and not renpy.game.preferences.mute['sfx']):
            try:
                # Blocking calls freeze screen and possibly cause voice channel to skip input
                passDialogue(what)
            except Exception as e:
                submod_log.error(str(e))
        RPY_SAY_FN(who, what, *args, **kwargs)

    def generateFrom0To2():
        seqId = -1
        while True:
            seqId = seqId + 1 if seqId < 2 else 0
            yield seqId

    def moveToThread(fn):
        t = threading.Thread(target=fn, name="MAS_xtts_loop")
        t.start()
        return t

    def textConsumer(speakerIdent):
        ctx = threading.local()
        pending = queue.Queue(3)
        speakerInfo = {
            "speaker_wav": speakerIdent,
            "language": "en"
        }
        def _requestTtsFile(text):
            # please pass in only non-empty, human friendly text
            iReq = _requestTtsFile._range.next()
            ctx.ttsFilePost['text'] = text
            ctx.ttsFilePost['file_name_or_path'] = ROOT_DIR + FILENAMES[iReq]
            try:
                return FILENAMES[iReq] if 200 == urllib2.urlopen(
                    urllib2.Request(
                        HOST_URL,
                        data=json.dumps(ctx.ttsFilePost),  #.encode('utf8'),
                        headers=REQ_HEAD),
                    timeout=10).getcode() else None
            except:  # offline, timeout or bad request
                return None
        _requestTtsFile._range = generateFrom0To2()
        
        def _requestTts(text):
            ctx.ttsFilePost['text'] = text
            res = None
            try:
                res = urllib2.urlopen(
                    urllib2.Request(
                        SRC_URL,
                        data=json.dumps(ctx.ttsFilePost),  #.encode('utf8'),
                        headers=REQ_HEAD),
                    timeout=10)
                if 200 == res.getcode():
                    return MASAudioData(res.read(), u"speech.wav")
            except:  # offline, timeout or bad request
                pass
            finally:
                if res:
                    res.close()
            return None
        
        def _processLoop():
            try:
                #if not hasattr(ctx, "ttsFilePost"):
                ctx.ttsFilePost = speakerInfo
                waitNext = False
                while True:
                    wait_voice = waitNext
                    try:
                        args = pending.get(block=True, timeout=30)
                    except queue.Empty:
                        return
                    if not args:
                        submod_log.debug(renpy.store.m_name + " left the channel")
                        return
                    
                    speech = args[0]
                    ctx.onReady = args[1]
                    if '{sustain}' == speech[:9]:
                        wait_voice = True
                        speech = speech[9:]
                    if '{complete}' == speech[-10:]:
                        waitNext = True
                        speech = speech[:-10]
                    else:
                        waitNext = False
                    speech = _requestTts(speech)
                    
                    if speech:  # a wave-filename
                        # Set next speech
                        # Queued voice is still stopped or skipped
                        #renpy.sound.queue(speech, channel='voice', clear_queue=False)
                        while wait_voice and renpy.sound.is_playing(channel="voice"):
                            time.sleep(1)
                        # Set or replace current speech
                        # store.voice starts speech on next interaction
                        #renpy.store.voice(speech)
                        # store.voice internally calls sound.play, which starts speech right away.
                        # However this does not support auto_voice or replay and does not reset volume.
                        renpy.sound.play(speech, channel='voice')
                    else:
                        submod_log.error("I don't know how to put this into words")
                    
                    VOICE_READY_EV.set()
                    if ctx.onReady:
                        ctx.onReady()
            except Exception as e:
                submod_log.error(str(e))
        
        t = threading.Thread()
        try:
            while True:
                try:
                    pending.put((yield), block=True, timeout=3)
                except queue.Full:  # On timeout
                    submod_log.warning("Sorry, am I speaking too fast?")
                if not t.isAlive():
                    t = moveToThread(_processLoop)
        except GeneratorExit:
            # Clear queue
            while not pending.empty():
                pending.get()
            pending.put(None)  # indicator to quit loop asap


    tts = textConsumer("en_f_JillianAshcraft-12s")
    tts.next()
    try:
        r = urllib2.Request(
            "http://localhost:8020/set_tts_settings",
            data=json.dumps({
                "temperature": 0.5,
                "length_penalty": 1,
                "repetition_penalty": 4,
                "top_k": 50,
                "top_p": 0.8,
                "speed": 1.1,
                "enable_text_splitting": False,
                "stream_chunk_size": 240
            }),
            headers=REQ_HEAD)
        if 200 == urllib2.urlopen(r, timeout=3).getcode():
            renpy.config.has_voice = True
    except:
        submod_log.exception("Didn't work, I got this")
