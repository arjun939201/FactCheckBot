from contextvars import ContextVar
from threading import Lock
from time import time

_current_id=ContextVar("research_progress_id",default=None)
_progress={}
_lock=Lock()
_STAGE_ORDER={"breaking":0,"researching":1,"collecting":2,"analyzing":3,"waiting_ai":3,"complete":4,"error":4}

def begin_progress(progress_id:str|None):
    _current_id.set(progress_id)
    if progress_id:
        with _lock:
            _progress[progress_id]={"stage":"breaking","updated_at":time()}

def update_progress(stage:str):
    progress_id=_current_id.get()
    if not progress_id:return
    with _lock:
        current=_progress.get(progress_id,{"stage":"breaking"})
        current_stage=current.get("stage","breaking")
        if stage=="waiting_ai":
            resume_stage=current.get("resume_stage",current_stage)
            _progress[progress_id]={"stage":"waiting_ai","resume_stage":resume_stage,"updated_at":time()}
            return
        if stage=="resume":
            if current_stage=="waiting_ai":
                _progress[progress_id]={"stage":current.get("resume_stage","breaking"),"updated_at":time()}
            return
        # Leaving a wait is allowed to resume at the correct stage, including
        # media extraction (which happens before web research starts).
        if current_stage=="waiting_ai" or _STAGE_ORDER.get(stage,0)>=_STAGE_ORDER.get(current_stage,0):
            _progress[progress_id]={"stage":stage,"updated_at":time()}

def get_progress(progress_id:str):
    with _lock:
        return dict(_progress.get(progress_id,{"stage":"unknown","updated_at":time()}))
