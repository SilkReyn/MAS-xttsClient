init 10 python:
    renpy.say = xttsClient.sayWithXtts
    store.mas_submod_utils.registerFunction("quit", xttsClient.tts.close)
