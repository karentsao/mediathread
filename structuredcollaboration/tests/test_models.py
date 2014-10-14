from django.test.testcases import TestCase

from mediathread.factories import MediathreadTestMixin, ProjectFactory
from structuredcollaboration.models import Collaboration


class ModelsTest(MediathreadTestMixin, TestCase):

    def setUp(self):
        self.setup_sample_course()

    def test_get_associated_collaboration_project(self):
        project = ProjectFactory.create(
            course=self.sample_course, author=self.student_one,
            policy='PrivateEditorsAreOwners')
        collaboration = Collaboration.get_associated_collab(project)
        self.assertIsNotNone(collaboration)

    def test_get_associated_collaboration_course(self):
        collaboration = Collaboration.get_associated_collab(self.sample_course)
        self.assertIsNotNone(collaboration)
