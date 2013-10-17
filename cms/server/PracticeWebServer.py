#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2013 Luca Versari <veluca93@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import io
import re
import json
import logging
import hashlib
import mimetypes
import pkg_resources
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy import desc

from cms import config, SOURCE_EXT_TO_LANGUAGE_MAP
from cms.log import initialize_logging
from cms.io.WebGeventLibrary import WebService
from cms.io import ServiceCoord
from cms.db.filecacher import FileCacher
from cms.db import SessionGen, User, Contest, Submission, File, Task
from cmscommon.DateTime import make_timestamp, make_datetime

from werkzeug.wrappers import Response, Request
from werkzeug.wsgi import SharedDataMiddleware, wrap_file, responder
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException, NotFound, BadRequest, \
    Unauthorized


logger = logging.getLogger(__name__)


class APIHandler(object):
    def __init__(self, parent):
        self.router = Map([
            Rule("/", methods=["GET", "POST"], endpoint="root"),
            Rule("/files/<digest>", methods=["GET", "POST"],
                 endpoint="dbfile"),
            Rule("/files/<digest>/<name>", methods=["GET", "POST"],
                 endpoint="dbfile"),
            Rule("/check", methods=["POST"], endpoint="check"),
            Rule("/register", methods=["POST"], endpoint="register"),
            Rule("/login", methods=["POST"], endpoint="login"),
            Rule("/tasks", methods=["POST"], endpoint="tasks"),
            Rule("/task/<name>", methods=["GET", "POST"], endpoint="task"),
            Rule("/submissions/<name>", methods=["POST"],
                endpoint="submissions"),
            Rule("/submission/<sid>", methods=["POST"], endpoint="submission"),
            Rule("/submit/<name>", methods=["POST"], endpoint="submit")
        ], encoding_errors="strict")
        self.file_cacher = parent.file_cacher
        self.contest = parent.contest
        self.evaluation_service = parent.evaluation_service
        self.EMAIL_REG = re.compile(r"[^@]+@[^@]+\.[^@]+")
        self.USERNAME_REG = re.compile(r"^[A-Za-z0-9_\.]+$")

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)

    @responder
    def wsgi_app(self, environ, start_response):
        route = self.router.bind_to_environ(environ)
        try:
            endpoint, args = route.match()
        except HTTPException:
            return NotFound()

        request = Request(environ)

        try:
            if endpoint == "root":
                return self.file_handler(environ, "index.html")
            elif endpoint == "dbfile":
                return self.dbfile_handler(environ, args)
            elif endpoint == "check":
                return self.check_handler(request)
            elif endpoint == "register":
                return self.register_handler(request)
            elif endpoint == "login":
                return self.login_handler(request)
            elif endpoint == "task":
                return self.task_handler(args["name"])
            elif endpoint == "tasks":
                return self.tasks_handler(request)
            elif endpoint == "submissions":
                return self.submissions_handler(request, args["name"])
            elif endpoint == "submission":
                return self.submission_handler(request, args["sid"])
            elif endpoint == "submit":
                return self.submit_handler(request, args["name"])
        except HTTPException as e:
            return e

        return NotFound()

    def get_user(self, session, contest, username, token):
        return session.query(User)\
            .filter(User.contest == contest)\
            .filter(User.username == username)\
            .filter(User.password == token).first()

    def get_req_user(self, session, contest, request, data=None):
        try:
            if data is None:
                data = self.load_json(request)
            username = data["username"]
            token = data["token"]
            user = self.get_user(session, contest, username, token)
            if user is None:
                raise Unauthorized()
            return user
        except (BadRequest, KeyError):
            raise Unauthorized()

    def load_json(self, request):
        if request.mimetype != "application/json":
            logger.warning("Request not in JSON")
            raise BadRequest()
        try:
            req = json.load(request.stream)
        except (ValueError, TypeError):
            logger.warning("JSON parse error")
            raise BadRequest()
        return req

    def dump_json(self, data):
        response = Response()
        response.mimetype = "application/json"
        response.status_code = 200
        response.data = json.dumps(data)
        return response

    def check_user(self, username):
        resp = dict()
        if len(username) < 4:
            resp["success"] = 0
            resp["error"] = "Username troppo corto"
        elif not self.USERNAME_REG.match(username):
            resp["success"] = 0
            resp["error"] = "Caratteri non validi nell'username"
        else:
            with SessionGen() as session:
                contest = Contest.get_from_id(self.contest, session)
                user = session.query(User)\
                    .filter(User.contest == contest)\
                    .filter(User.username == username).first()
                if user is None:
                    resp["success"] = 1
                else:
                    resp["success"] = 0
                    resp["error"] = "Username gia' esistente"
        return resp

    def check_email(self, email):
        resp = dict()
        if not self.EMAIL_REG.match(email):
            resp["success"] = 0
            resp["error"] = "Indirizzo email non valido"
        else:
            with SessionGen() as session:
                contest = Contest.get_from_id(self.contest, session)
                user = session.query(User)\
                    .filter(User.contest == contest)\
                    .filter(User.email == email).first()
                if user is None:
                    resp["success"] = 1
                else:
                    resp["success"] = 0
                    resp["error"] = "Email gia' esistente"
        return resp

    def file_handler(self, environ, filename):
        path = os.path.join(
            pkg_resources.resource_filename("cms.web", "practice"),
            filename)

        response = Response()
        response.status_code = 200
        response.mimetype = "application/octect-stream"
        mimetype = mimetypes.guess_type(filename)[0]
        if mimetype is not None:
            response.mimetype = mimetype
        response.last_modified = \
            datetime.utcfromtimestamp(os.path.getmtime(path))\
                    .replace(microsecond=0)
        response.response = wrap_file(environ, io.open(path, 'rb'))
        response.direct_passthrough = True
        return response

    def dbfile_handler(self, environ, args):
        try:
            fobj = self.file_cacher.get_file(args["digest"])
        except KeyError:
            raise NotFound()

        response = Response()
        response.status_code = 200

        response.mimetype = "application/octect-stream"
        if "filename" in args:
            response.headers.add_header(
                b'Content-Disposition', b'attachment',
                filename=args["filename"])
            mimetype = mimetypes.guess_type(args['filename'])[0]
            if mimetype is not None:
                response.mimetype = mimetype

        response.response = wrap_file(environ, fobj)
        response.direct_passthrough = True
        return response

    def check_handler(self, request):
        req = self.load_json(request)
        try:
            rtype = req["type"]
            rvalue = req["value"]
        except KeyError:
            logger.warning("Missing parameters")
            raise BadRequest()

        if rtype == "username":
            return self.dump_json(self.check_user(rvalue))
        elif rtype == "email":
            return self.dump_json(self.check_email(rvalue))

        logger.warning("Request type not understood")
        raise BadRequest()

    def register_handler(self, request):
        req = self.load_json(request)
        try:
            username = req["username"]
            password = req["password"]
            email = req["email"]
            firstname = req["firstname"]
            lastname = req["lastname"]
        except KeyError:
            logger.warning("Missing parameters")
            raise BadRequest()

        sha = hashlib.sha256()
        sha.update(password)
        sha.update(config.secret_key)
        token = sha.hexdigest()

        resp = self.check_user(username)
        if not resp["success"]:
            return self.dump_json(resp)
        resp = self.check_email(email)
        if not resp["success"]:
            return self.dump_json(resp)

        resp["success"] = 1
        with SessionGen() as session:
            user = User(
                first_name=firstname,
                last_name=lastname,
                username=username,
                password=token,
                email=email
            )
            user.contest = Contest.get_from_id(self.contest, session)
            try:
                session.add(user)
                session.commit()
            except IntegrityError:
                resp["success"] = 0
                resp["error"] = "USER_EXISTS"
        return self.dump_json(resp)

    def login_handler(self, request):
        req = self.load_json(request)
        try:
            username = req["username"]
            password = req["password"]
        except KeyError:
            logger.warning("Missing parameter")
            raise BadRequest()

        sha = hashlib.sha256()
        sha.update(password)
        sha.update(config.secret_key)
        token = sha.hexdigest()

        resp = dict()
        with SessionGen() as session:
            contest = Contest.get_from_id(self.contest, session)
            user = self.get_user(session, contest, username, token)
            if user is None:
                resp["success"] = 0
            else:
                resp["success"] = 1
                resp["token"] = token
        return self.dump_json(resp)

    def tasks_handler(self, request):
        data = self.load_json(request)
        if data["first"] > data["last"]:
            raise BadRequest()
        resp = dict()
        with SessionGen() as session:
            contest = Contest.get_from_id(self.contest, session)
            tasks = session.query(Task)\
                .filter(Task.contest_id == contest.id)\
                .order_by(desc(Task.id))\
                .slice(data["first"], data["last"]).all()
            resp["num"] = session.query(Task)\
                .filter(Task.contest_id == contest.id).count()
            resp["tasks"] = []
            for t in tasks:
                task = dict()
                task["id"] = t.id
                task["name"] = t.name
                task["title"] = t.title
                resp["tasks"].append(task)
        return self.dump_json(resp)

    def task_handler(self, name):
        resp = dict()
        with SessionGen() as session:
            contest = Contest.get_from_id(self.contest, session)
            t = session.query(Task)\
                .filter(Task.name == name)\
                .filter(Task.contest_id == contest.id)\
                .first()
            resp["id"] = t.id
            resp["name"] = t.name
            resp["title"] = t.title
            resp["statements"] =\
                dict([(l, s.digest) for l, s in t.statements.iteritems()])
            resp["submission_format"] =\
                [sfe.filename for sfe in t.submission_format]
            for i in ["time_limit", "memory_limit", "task_type"]:
                resp[i] = getattr(t.active_dataset, i)
            att = dict()
            for (name, obj) in t.attachments.iteritems():
                att[name] = obj.digest
            resp["attachments"] = att
        return self.dump_json(resp)

    def submissions_handler(self, request, name):
        resp = dict()
        with SessionGen() as session:
            contest = Contest.get_from_id(self.contest, session)
            user = self.get_req_user(session, contest, request)
            task = session.query(Task)\
                .filter(Task.contest_id == contest.id)\
                .filter(Task.name == name).first()
            if task is None:
                raise NotFound()
            subs = session.query(Submission)\
                .filter(Submission.user_id == user.id)\
                .filter(Submission.task_id == task.id)\
                .order_by(desc(Submission.timestamp)).all()
            submissions = []
            for s in subs:
                submission = dict()
                submission["id"] = s.id
                submission["task_id"] = s.task_id
                submission["timestamp"] = make_timestamp(s.timestamp)
                submission["files"] = []
                for name, f in s.files.iteritems():
                    fi = dict()
                    fi["name"] = name.replace("%l", s.language)
                    fi["digest"] = f.digest
                    submission["files"].append(fi)
                result = s.get_result()
                for i in ["compilation_outcome", "evaluation_outcome",
                          "score"]:
                    submission[i] = getattr(result, i, None)
                submissions.append(submission)
        resp["submissions"] = submissions
        return self.dump_json(resp)

    def submission_handler(self, request, sid):
        with SessionGen() as session:
            contest = Contest.get_from_id(self.contest, session)
            user = self.get_req_user(session, contest, request)
            s = session.query(Submission)\
                .filter(Submission.id == sid).first()
            if s is None:
                raise NotFound()
            if s.user_id != user.id:
                raise Unauthorized()
            submission = dict()
            submission["id"] = s.id
            submission["task_id"] = s.task_id
            submission["timestamp"] = make_timestamp(s.timestamp)
            submission["language"] = s.language
            submission["files"] = []
            for name, f in s.files.iteritems():
                fi = dict()
                fi["name"] = name.replace("%l", s.language)
                fi["digest"] = f.digest
                submission["files"].append(fi)
            result = s.get_result()
            for i in ["compilation_outcome", "evaluation_outcome",
                      "score", "compilation_stdout", "compilation_stderr",
                      "compilation_time", "compilation_memory"]:
                submission[i] = getattr(result, i, None)
            if result.score_details is not None:
                tmp = json.loads(result.score_details)
                if len(tmp)>0 and tmp[0].has_key("text"):
                    subt = dict()
                    subt["testcases"] = tmp
                    subt["score"] = submission["score"]
                    subt["max_score"] = 100
                    submission["score_details"] = [subt]
                else:
                    submission["score_details"] = tmp
                for subtask in submission["score_details"]:
                    for testcase in subtask["testcases"]:
                        data = json.loads(testcase["text"])
                        testcase["text"] = data[0] % tuple(data[1:])
            else:
                submission["score_details"] = None
            return self.dump_json(submission)

    def submit_handler(self, request, task_name):
        with SessionGen() as session:
            data = self.load_json(request)
            contest = Contest.get_from_id(self.contest, session)
            user = self.get_req_user(session, contest, request, data=data)
            lastsub = session.query(Submission)\
                .filter(Submission.user_id == user.id)\
                .order_by(desc(Submission.timestamp)).first()
            if lastsub is not None and \
               make_datetime() - lastsub.timestamp < timedelta(seconds=20):
                resp = dict()
                resp["success"] = 0
                resp["error"] = "E' passato troppo poco tempo!"
                return self.dump_json(resp)

            # TODO: implement checks (size), archives and
            # (?) partial submissions
            try:
                task = contest.get_task(task_name)
            except KeyError:
                raise NotFound()
            resp = dict()
            resp["success"] = 1

            # Detect language
            files = []
            sub_language = None
            for sfe in task.submission_format:
                f = data["files"].get(sfe.filename)
                if f is None:
                    resp["success"] = 0
                    resp["error"] = "Mancano dei files!"
                    return self.dump_json(resp)
                f["name"] = sfe.filename
                files.append(f)
                if sfe.filename.endswith(".%l"):
                    language = None
                    for ext, lang in SOURCE_EXT_TO_LANGUAGE_MAP.iteritems():
                        if f["filename"].endswith(ext):
                            language = lang
                    if language is None:
                        resp["success"] = 0
                        resp["error"] = "Linguaggio non riconosciuto"
                        return self.dump_json(resp)
                    elif sub_language is not None and sub_language != language:
                        resp["success"] = 0
                        resp["error"] = "Files di linguaggio diverso!"
                        return self.dump_json(resp)
                    else:
                        sub_language = language

            # Add the submission
            timestamp = make_datetime()
            submission = Submission(timestamp,
                                    sub_language,
                                    user=user,
                                    task=task)
            for f in files:
                digest = self.file_cacher.put_file_content(
                    bytes(f["data"]),
                    "Submission file %s sent by %s at %d." % (
                        f["name"], user.username,
                        make_timestamp(timestamp)))
                session.add(File(f["name"], digest, submission=submission))
            session.add(submission)
            session.commit()

            # Notify ES
            self.evaluation_service.new_submission(submission_id=submission.id)

            # Answer with submission data
            resp["id"] = submission.id
            resp["task_id"] = submission.task_id
            resp["timestamp"] = make_timestamp(submission.timestamp)
            resp["compilation_outcome"] = None
            resp["evaluation_outcome"] = None
            resp["score"] = None
            resp["files"] = []
            for name, f in submission.files.iteritems():
                fi = dict()
                fi["name"] = name.replace("%l", submission.language)
                fi["digest"] = f.digest
                resp["files"].append(fi)
            return self.dump_json(resp)


class PracticeWebServer(WebService):
    """Service that runs the web server for practice.

    """
    def __init__(self, shard, contest):
        initialize_logging("PracticeWebServer", shard)

        WebService.__init__(
            self,
            config.contest_listen_port[shard],
            [],
            {},
            shard=shard,
            listen_address=config.contest_listen_address[shard])

        self.file_cacher = FileCacher(self)
        self.contest = contest
        self.evaluation_service = self.connect_to(
            ServiceCoord("EvaluationService", 0))

        handler = APIHandler(self)

        self.wsgi_app = SharedDataMiddleware(handler, {
            '/':        ("cms.web", "practice"),
            '/assets':  ("cms.web", "assets")
        })
