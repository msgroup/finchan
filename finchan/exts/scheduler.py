# -*- coding: utf-8 -*-
# This file is part of finchan.

# Copyright (C) 2017-present qytz <hhhhhf@foxmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Scheduler event source, interface inspired by `schedule <https://github.com/dbader/schedule>`_

usage::

    def setup(env)
        scheduler = env.scheduler
        # run once @10:00
        scheduler.at("10:00").do(timer_call)
        # run everyday @09:31
        scheduler.every(1).days.at('09:31').tag('daily_task').do(timer_call)
        # runs on every two weeks' friday @09:31
        scheduler.every(2).weeks.at('friday 09:31').tag('daily_task').do(timer_call)
        # runs on every month's day 09 @09:31
        scheduler.every(1).months.at('09 09:31').tag('daily_task').do(timer_call)


    def clean(env)
        scheduler = env.scheduler
        scheduler.cancel_all_jobs()

"""
import random
import asyncio
import logging
import collections
from datetime import datetime
from dateutil.parser import parse as parse_dt
from dateutil.relativedelta import relativedelta as timedelta

from finchan.event import Event
from finchan.utils import get_id_gen
from finchan.interface.event_source import AbsEventSource


# name of the extension
ext_name = "finchan.exts.scheduler"
# required extension
required_exts = []

logger = logging.getLogger(__name__)


async def event_callback(event):
    """call scheduler func in event callback"""
    func = event.kwargs["func"]
    return await func(event.env, *event.kwargs["args"], **event.kwargs["kwargs"])


class JobMananger(object):
    """
    Objects instantiated by the :class:`JobMananger <JobMananger>` are
    factories to create jobs, keep record of scheduled jobs and
    handle their execution.

    TODO: schedule with asyncio.sleep(idle_seconds) and loop.create_task and task.cancel.
    """

    def __init__(self, env):
        self.env = env
        self.jobs = []
        self.curr_job = None

    def at(self, dt):
        """Schedule a new job that only runs once time.

        :param dt: the `datetime <datetime.datetime>` object that the job will run.
        :return: An unconfigured :class:`Job <Job>`
        """
        job = Job(step=0, job_manager=self, unit="once")
        return job.at(offset_dt=dt)

    def every(self, step=1):
        """
        Schedule a new periodic job.

        :param step: A quantity of a certain time unit
        :return: An unconfigured :class:`Job <Job>`
        """
        job = Job(step=step, job_manager=self)
        return job

    def add_job(self, job):
        """Add a new job to the `JobMananger`"""
        self.jobs.append(job)

    def cancel(self, job):
        """cancel a job's schedule"""
        try:
            self.jobs.remove(job)
        except ValueError:
            pass

    def clear(self, tag=None):
        """
        Deletes scheduled jobs marked with the given tag, or all jobs
        if tag is `None`.

        :param tag: An identifier used to identify a subset of
                    jobs to delete
        """
        if tag is None:
            del self.jobs[:]
        else:
            self.jobs[:] = (job for job in self.jobs if tag not in job.tags)

    def cancel_all_jobs(self):
        """cancel all jobs"""
        self.jobs.clear()

    def get_next_job(self):
        """Get next job to run"""
        if not self.jobs:
            return None
        return min(self.jobs)

    def idle_seconds(self):
        """idle seconds before next job should run"""
        next_job = self.get_next_job()
        return (next_job.next_run - self.env.now).total_seconds()

    async def schedule(self):
        """TODO: implement this."""
        pass


class Job(object):
    """
    A periodic job as used by :class:`JobMananger`.

    :param step: A quantity of a certain time unit
    :param job_manager: The :class:`JobMananger <JobMananger>` instance that
                      this job will register itself with once it has
                      been fully configured in :meth:`Job.do()`.
    :param unit: perioid unit, specified below.
    :param job_id: the ID of the job object, can be None, system will generate one for you.

    Every job runs at a given fixed time interval that is defined by:

    * a :meth:`time unit second <Job.seconds>` or :meth:`time unit minite <Job.minutes>` etc.
    * a quantity of `time units` defined by `step`

    A job is usually created and returned by :meth:`JobMananger.every` method,
    which also defines its `interval` step, or by :meth:`JobMananger.at` method,
    which specified its run time.

    unit types:

        * once
        * seconds
        * minutes
        * hours
        * days
        * weeks
        * months
        * years
    """

    id_gen = get_id_gen(prefix="Job")
    unit_types = ["once", "seconds", "minutes", "hours", "days", "weeks", "months", "years"]

    def __init__(self, step, job_manager, unit=None, job_id=None):
        self.job_id = job_id
        self.unit = unit
        self.next_run = None
        self.job_manager = job_manager
        self.env = job_manager.env
        self.min_step = self.max_step = step

        self.last_run = None
        self.job_func = None
        self.event_kwargs = None
        self.tags = set()

        self.offset_dt = parse_dt("1970-01-01 00:00:00")

        if not self.job_id:
            self.job_id = next(Job.id_gen)

        self.event_name = "Scheduler.%s" % (self.job_id)
        self.event_kwargs = {}

    def to(self, step):
        """
        Schedule the job to run at an irregular (randomized) step.

        The job's interval will randomly vary from the value given
        to  `every <JobMananger.every>` to `step`.
        The range defined is inclusive on both ends.
        For example, `every(A).to(B).seconds` executes
        the job function every N seconds such that A <= N <= B.

        :param step: Maximum interval between randomized job runs
        :return: The invoked job instance
        """
        self.max_step = int(step)
        assert self.max_step >= self.min_step
        return self

    def seconds(self):
        """run in second steps

        :return: The job instance
        """
        self.unit = "seconds"
        return self

    def minutes(self):
        """run in minute steps

        :return: The job instance
        """
        self.unit = "minutes"
        return self

    def hours(self):
        """run in hour steps

        :return: The job instance
        """
        self.unit = "hours"
        return self

    def days(self):
        """run in day steps

        :return: The job instance
        """
        self.unit = "days"
        return self

    def weeks(self):
        """run in week steps

        :return: The job instance
        """
        self.unit = "weeks"
        return self

    def months(self):
        """run in month steps

        :return: The job instance
        """
        self.unit = "months"
        return self

    def years(self):
        """run in year steps

        :return: The job instance
        """
        self.unit = "years"
        return self

    def tag(self, *tags):
        """
        Tags the job with one or more unique indentifiers.

        Tags must be hashable. Duplicate tags are discarded.

        :param tags: A unique list of ``Hashable`` tags.
        :return: The invoked job instance
        """
        if not all(isinstance(tag, collections.Hashable) for tag in tags):
            raise TypeError("Tags must be hashable")
        self.tags.update(tags)
        return self

    def at(self, offset_dt=None):
        """
        Schedule the job every step hours/days/weeks/months at a specific offset.

        :param offset_dt: a `datatime` str, like '1970-01-01 00:00:00', 'monday',
                          '09:32', '07-21', '21 14:12' and so on,
                          which must can be parsed by `dateutil.parser.parse`

        picks:

            * for hours: pick the offset_dt's minite and second field.
            * for days: pick the offset_dt's time field.
            * for weeks: pick the offset_dt's time field and weekday field.
            * for months: pick the offset_dt's time field and day field.

        :return: The invoked job instance
        """
        if not offset_dt:
            logger.warning("#Scheduler Call offset but no offset_dt specified.")
            return self

        try:
            self.offset_dt = parse_dt(offset_dt)
        except (ValueError, TypeError):
            logger.warning(
                "#Scheduler offset for time in invalid: %s, set to default: %s",
                offset_dt,
                self.offset_dt,
            )
        return self

    def do(self, func, *args, **kwargs):
        """
        Specifies the func that should be called every time the job runs.

        Any additional arguments are passed on to func when the job runs.

        Actually, the func is wrapped in an event callback function,
        when it's time to call the function, the `Scheduler` just generate the event,
        the function will be executed when the `Dispatcher` dispatch the event.

        :param func: The function to be scheduled
        :return: The invoked job instance
        """
        assert self.unit in Job.unit_types
        self.event_kwargs.update({"func": func, "args": args, "kwargs": kwargs})

        self._schedule_first_run()
        logger.debug("#Scheduler Job do last_run: %s next_run: %s", self.last_run, self.next_run)
        self.job_manager.add_job(self)
        logger.info("#Scheduler New Job added: %s", self)
        self.job_manager.env.dispatcher.subscribe(self.event_name, event_callback)
        return self

    def gen_event(self):
        """Generate the event and schedule the next time of the event occurs."""
        event = Event(self.env, self.event_name, dt=self.next_run, **self.event_kwargs)
        if self.unit == "once":
            self.job_manager.cancel(self)
        else:
            self.last_run = self.next_run
            self._schedule_next_run()

        logger.debug(
            "#Scheduler generate new event %s, job last run: %s next run: %s",
            event,
            self.last_run,
            self.next_run,
        )

        return event

    def __lt__(self, other):
        """
        PeriodicJobs are sortable based on the scheduled time they
        run next.
        """
        return self.next_run < other.next_run

    def _schedule_first_run(self):
        """Compute the instant when this job should run the first time."""
        if self.unit == "once":
            self.next_run = self.offset_dt
            return

        if self.max_step is not None:
            step = random.randint(self.min_step, self.max_step)
        else:
            step = self.min_step

        replay_dict = {}
        offset_dict = {}
        env = self.job_manager.env
        if self.offset_dt.year < env.now.year:
            replay_dict["year"] = env.now.year

        if self.unit == "weeks":
            offset_dict["days"] = step * 7
        else:
            offset_dict[self.unit] = step

        if self.unit == "minutes":
            replay_dict["second"] = self.offset_dt.second
        if self.unit == "hours":
            replay_dict["minute"] = self.offset_dt.minute
            replay_dict["second"] = self.offset_dt.second
        if self.unit == "days":
            replay_dict["hour"] = self.offset_dt.hour
            replay_dict["minute"] = self.offset_dt.minute
            replay_dict["second"] = self.offset_dt.second
        if self.unit == "months":
            replay_dict["day"] = self.offset_dt.day
            replay_dict["hour"] = self.offset_dt.hour
            replay_dict["minute"] = self.offset_dt.minute
            replay_dict["second"] = self.offset_dt.second
        if self.unit == "years":
            replay_dict["month"] = self.offset_dt.month
            replay_dict["day"] = self.offset_dt.day
            replay_dict["hour"] = self.offset_dt.hour
            replay_dict["minute"] = self.offset_dt.minute
            replay_dict["second"] = self.offset_dt.second

        bench_dt = env.now.replace(**replay_dict)
        self.next_run = bench_dt + timedelta(**offset_dict)
        while self.next_run < env.now:
            self.last_run = self.next_run
            self._schedule_next_run()

    def _schedule_next_run(self):
        """Compute the instant when this job should run next."""
        if self.unit == "once":
            return True

        if self.max_step is not None:
            step = random.randint(self.min_step, self.max_step)
        else:
            step = self.min_step
        self.next_run = self.last_run + timedelta(**{self.unit: step})


class LiveScheduler(AbsEventSource, JobMananger):
    """Live SchedulerEventSource, used for ``live`` mode"""

    _name = "LiveScheduleEventSource"

    def __init__(self, env, *args, **kwargs):
        """init the event source"""
        self.env = env
        super().__init__(*args, **kwargs)
        self.job_manager = JobMananger(env)

    @property
    def name(self):
        """Name of the event_source"""
        return self._name

    async def gen_events(self, limit_dt=None):
        dispatcher = self.env.dispatcher
        while True:
            next_job = self.job_manager.get_next_job()
            if next_job and next_job.next_run <= self.env.now:
                await dispatcher.put_event(next_job.gen_event())
                if next_job.unit == "once":
                    self.job_manager.cancel(next_job)
            else:
                # logger.debug('#Scheduler no job yet, sleep 1s.')
                await asyncio.sleep(1)

    def start(self):
        """initialize the event_source, start generate/receive events

        event_q: the event queue to put events to.
        """
        pass

    def stop(self):
        """stop the event_source, stop generate/receive events"""
        logger.info("#Scheduler stopping, will clear all jobs.")
        self.job_manager.cancel_all_jobs()
        logger.debug("#Scheduler stoped.")


class BackTrackScheduler(AbsEventSource, JobMananger):
    """BackTrack ScheduleEventSource, used for ``backtrack`` mode"""

    _name = "BackTrackScheduleEventSource"

    def __init__(self, env, *args, **kwargs):
        """init the event source"""
        self.env = env
        self.job_manager = JobMananger(env)

    @property
    def name(self):
        """Name of the event_source"""
        return self._name

    async def gen_events(self, limit_dt=None):
        dispatcher = self.env.dispatcher
        while True:
            next_job = self.job_manager.get_next_job()
            if limit_dt and next_job and next_job.next_run > limit_dt:
                break
            await dispatcher.put_event(next_job.gen_event())
            if next_job.unit == "once":
                self.job_manager.cancel(next_job)

    def start(self):
        """initialize the event_source, start generate/receive events

        event_q: the event queue to put events to.
        """
        pass

    def stop(self):
        """stop the event_source, stop generate/receive events"""
        pass


def load_finchan_ext(env, *args, **kwargs):
    if env.run_mode == "backtrack":
        scheduler = BackTrackScheduler(env)
    else:
        scheduler = LiveScheduler(env)
    env.ext_space.scheduler = scheduler.job_manager
    env.dispatcher.register_event_source(scheduler)


def unload_finchan_ext(env):
    if env.ext_space.scheduler:
        env.ext_space.scheduler.cancel_all_jobs()
        env.dispatcher.deregister_event_source(env.ext_space.scheduler)
        env.ext_space.scheduler = None


if __name__ == "__main__":
    # tests
    pass