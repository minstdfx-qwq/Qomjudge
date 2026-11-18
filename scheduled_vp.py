"""Server-owned one-shot VP timers. No automatic retry of upstream starts."""
from copy import deepcopy
import math
import secrets
import threading
import time


class ScheduleError(ValueError):
    status = 409


class ScheduledVP:
    def __init__(self, launch, clock=time.time):
        self.launch, self.clock = launch, clock
        self.lock = threading.RLock()
        self.jobs = {}

    def view(self, owner):
        with self.lock:
            job = self.jobs.get(owner)
            return {k: deepcopy(job[k]) for k in ('id', 'target', 'status', 'message')} if job else None

    def guard(self, owner):
        if (self.view(owner) or {}).get('status') in {'pending', 'starting'}:
            raise ScheduleError('已设定定时 VP，请先取消定时安排再执行此操作。')

    def schedule(self, owner, target, binding):
        if (isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target)
                or not 3 <= target - self.clock() <= 7 * 86400):
            raise ScheduleError('开赛时间须在当前时间至少 3 秒后、7 天以内。')
        with self.lock:
            self.guard(owner)
            job = dict(id=secrets.token_urlsafe(16), target=target, status='pending', message='',
                       binding=deepcopy(binding), cancel=threading.Event())
            self.jobs[owner] = job
            threading.Thread(target=self._wait, args=(owner, job), name='qoj-scheduled-vp', daemon=True).start()
            return self.view(owner)

    def _wait(self, owner, job):
        while not job['cancel'].is_set():
            remaining = job['target'] - self.clock()
            if remaining <= 0:
                self.fire(owner, job['id'])
                return
            job['cancel'].wait(min(remaining, 1))

    def fire(self, owner, identity):
        with self.lock:
            job = self.jobs.get(owner)
            if not job or job['id'] != identity or job['status'] != 'pending' or self.clock() < job['target']:
                return
            job['status'] = 'starting'
        try:
            result = self.launch(owner, deepcopy(job['binding']))
            status = result.get('status')
            if result.get('entry_uncertain') or status not in {'scheduled', 'running', 'finished'}:
                raise ScheduleError(result.get('message') or 'QOJ 尚未确认开赛，请手动刷新核查；不会自动重复开赛。')
        except Exception as exc:
            with self.lock:
                job.update(status='error', message=str(exc))
        else:
            with self.lock:
                job.update(status='started', message='QOJ 已确认开赛。')
        finally:
            with self.lock:
                job.pop('binding', None)

    def cancel(self, owner):
        with self.lock:
            job = self.jobs.get(owner)
            if not job:
                return None
            if job['status'] == 'starting':
                raise ScheduleError('开赛请求正在发送，无法取消；请等待 QOJ 确认。')
            job['cancel'].set()
            job.update(status='cancelled', message='定时 VP 已取消。')
            job.pop('binding', None)
            return self.view(owner)

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                job['cancel'].set()
