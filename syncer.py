#!/usr/bin/env python

import sys
import syslog
import urllib2
import os
import re
import fcntl
import time

try:
    import json
except ImportError:
    import simplejson as json

class SimpleLogger(object):
    """
    A Simple logger
    """

    def __init__(self):
        # Open syslog for logging
        syslog.openlog("pagerduty_python")

    # Some utility functions for logging
    def info(self, message):
        self.log(syslog.LOG_INFO, message)

    def warn(self, message):
        self.log(syslog.LOG_WARNING, message)

    def error(self, message):
        self.log(syslog.LOG_ERR, message)

    def log(self, level, message):
        # print(message)
        syslog.syslog(level, message)

logger = SimpleLogger()

class PagerDutyClient(object):
    """
    A simple client that can submit events (a file based event) to PagerDuty.
    """

    EVENTS_API_BASE = "https://events.pagerduty.com/generic/2010-04-15/create_event.json"

    def __init__(self, api_base=EVENTS_API_BASE):
        self.api_base = api_base

    def submit_event(self, file_path):
        json_event = None
        with open(file_path, "r") as event_file:
            json_event = event_file.read()

        incident_key = None
        retry = False

        try:
            request = urllib2.Request(self.api_base)
            request.add_header("Content-type", "application/json")
            request.add_data(json_event)
            response = urllib2.urlopen(request)
            result = json.loads(response.read())

            if result["status"] == "success":
                incident_key = result["incident_key"]
            else:
                logger.warn("PagerDuty server REJECTED the event in file: %s, Reason: %s" % (file_path, str(response)))

        except urllib2.URLError as e:
            # client error
            if e.code >= 400 and e.code < 500:
                logger.warn("PagerDuty server REJECTED the event in file: %s, Reason: %s" % (file_path, e.read()))
            else:
                logger.warn("DEFERRED PagerDuty event in file: %s, Reason: [%s, %s]" % (file_path, e.code, e.reason))
                retry = True # We'll need to retry

        return (retry, incident_key)


class PagerDutyQueue(object):
    """
    This class implements a simple directory based queue for PagerDuty events
    """

    QUEUE_DIR = "/tmp/pagerduty"

    def __init__(self, queue_dir=QUEUE_DIR, pagerduy_client=PagerDutyClient()):
        self.queue_dir = queue_dir
        self.pagerduy_client = pagerduy_client
        self._create_queue_dir()
        self._verify_permissions()

    def _create_queue_dir(self):
        if not os.access(self.queue_dir, os.F_OK):
            os.mkdir(self.queue_dir, 0700)

    def _verify_permissions(self):
        if not (os.access(self.queue_dir, os.R_OK)
            and os.access(self.queue_dir, os.W_OK)):
            logger.error("Can't read/write to directory %s, please check permissions." % self.queue_dir)
            raise Exception("Can't read/write to directory %s, please check permissions." % self.queue_dir)

    # Get the list of files from the queue directory
    def _queued_files(self):
        files = os.listdir(self.queue_dir)
        pd_names = re.compile("pd_")
        pd_file_names = filter(pd_names.match, files)

        # We need to sort the files by the timestamp.
        # This function extracts the timestamp out of the file name
        def file_timestamp(file_name):
            return int(re.search('pd_(\d+)_', file_name).group(1))

        sorted_file_names = sorted(pd_file_names, key=file_timestamp)
        return pd_file_names

    def _flush_queue(self):
        file_names = self._queued_files()
        for file_name in file_names:
            file_path = ("%s/%s" % (self.queue_dir, file_name))
            retry, incident_key = self.pagerduy_client.submit_event(file_path)

            if not retry:
                os.remove(file_path)

            if incident_key:
                logger.info("PagerDuty event submitted with incident key: %s" % incident_key)

    def lock_and_flush_queue(self):
        with open("%s/lockfile" % self.queue_dir, "w") as lock_file:
            try:
                logger.info("Acquiring lock on queue")
                fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # We have acquired the lock here
                # Let's flush the queue
                self._flush_queue()
            except IOError as e:
                logger.warn("Error while trying to acquire lock on queue: %s" % str(e))
            finally:
                logger.info("Releasing lock on queue")
                fcntl.lockf(lock_file.fileno(), fcntl.LOCK_UN)

    def enqueue(self, event):
        encoded_event = json.dumps(event)
        process_id = os.getpid()
        time_seconds = int(time.time())
        file_name = "%s/pd_%d_%d" % (self.queue_dir, time_seconds, process_id)
        logger.info("Queuing event %s" % str(event))
        with open(file_name, "w", 0600) as f:
            f.write(encoded_event)

class Zabbix(object):
    """
    Zabbix integration
    """

    def __init__(self, arguments):
        self.arguments = arguments

    # Parse the Zabbix message body. The body MUST be in this format:
    #
    # name:{TRIGGER.NAME}
    # id:{TRIGGER.ID}
    # status:{TRIGGER.STATUS}
    # hostname:{HOSTNAME}
    # ip:{IPADDRESS}
    # value:{TRIGGER.VALUE}
    # event_id:{EVENT.ID}
    # severity:{TRIGGER.SEVERITY}
    #
    def _parse_zabbix_body(self, body_str):
        return dict(line.strip().split(':', 1) for line in body_str.strip().split('\n'))

    # Parse the Zabbix message subject.
    # The subject MUST be one of the following:
    #
    # trigger
    # resolve
    #
    def _parse_zabbix_subject(self, subject_str):
        return subject_str

    def event(self):
        # The first argument is the service key
        service_key = self.arguments[1]
        # The second argument is the message type
        message_type = self._parse_zabbix_subject(self.arguments[2])
        event = self._parse_zabbix_body(self.arguments[3])
        logger.info("event %s" % event)

        # Incident key is created by concatenating trigger id and host name.
        # Remember, incident key is used for de-duping and also to match
        # trigger with resolve messages
        incident_key = "%s-%s" % (event["id"], event["hostname"])

        # The description that is rendered in PagerDuty and also sent as SMS
        # and phone alert
        description = "%s : %s for %s" % (event["name"],
                        event["status"], event["hostname"])

        pagerduty_event = {
            "service_key": service_key, "event_type":message_type,
            "description": description, "incident_key": incident_key,
            "details": event
        }
        return pagerduty_event


# If the length of the arguments is 4 then assume it was invoked from
# Zabbix, otherwise, just try to flush the queue
if __name__ == "__main__":
    pagerduty_queue = PagerDutyQueue()
    if len(sys.argv) == 4:
        pagerduty_queue.enqueue(Zabbix(sys.argv).event())
    pagerduty_queue.lock_and_flush_queue()
