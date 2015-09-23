from hashlib import sha1
from courseaffils.models import Course
from django.contrib.auth.models import User, Group
from django.shortcuts import get_object_or_404
from pylti.common import LTIException

from lti_auth.lti import LTI


class LTIBackend(object):

    def create_user(self, lti, username):
        # create the user if necessary
        user = User(username=username, password='LTI user')
        user.email = lti.user_email()
        user.last_name = lti.user_fullname()
        user.set_unusable_password()
        user.save()
        return user

    def get_hashed_username(self, lti):
            # (http://developers.imsglobal.org/userid.html)
        # generate a username to avoid overlap with existing system usernames
        # sha1 hash result + trunc to 30 chars should result in a valid
        # username with low-ish-chance of collisions
        uid = lti.consumer_user_id()
        return sha1(uid).hexdigest()[:30]

    def find_or_create_user(self, lti):
        try:
            # find the user via email address
            user = User.objects.get(email=lti.user_email())
        except User.DoesNotExist:
            username = self.get_hashed_username(lti)
            try:
                # find the user via generated lti user id
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                # finally, create a new lti user
                user = self.create_user(lti, username)

        return user

    def join_course(self, lti, course, user):
        # add the user to the requested course
        user.groups.add(course.group)
        for role in lti.user_roles():
            if role.lower() in ['staff', 'instructor', 'administrator']:
                user.groups.add(course.faculty_group)
                break

    def authenticate(self, request=None, request_type=None, role_type=None):
        if not request or not request_type or not role_type:
            return None

        try:
            lti = LTI(request_type, role_type)
            lti.verify(request)

            # validate course first
            try:
                coursename = lti.course_group()
                group = get_object_or_404(Group, name=coursename)
                course = group.course
            except Course.DoesNotExist:
                return None

            user = self.find_or_create_user(lti)

            self.join_course(lti, course, user)

            return user

        except LTIException:
            return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
