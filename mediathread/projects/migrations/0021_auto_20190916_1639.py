# -*- coding: utf-8 -*-
# Generated by Django 1.11.24 on 2019-09-16 16:39
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0020_project_summary'),
    ]

    operations = [
        migrations.AlterField(
            model_name='project',
            name='due_date',
            field=models.DateTimeField(blank=True, null=True, verbose_name='due date'),
        ),
        migrations.AlterField(
            model_name='project',
            name='modified',
            field=models.DateTimeField(auto_now=True, verbose_name='date modified'),
        ),
        migrations.AlterField(
            model_name='project',
            name='participants',
            field=models.ManyToManyField(blank=True, related_name='projects', to=settings.AUTH_USER_MODEL, verbose_name='Authors'),
        ),
        migrations.AlterField(
            model_name='project',
            name='project_type',
            field=models.TextField(choices=[('assignment', 'Composition Assignment'), ('composition', 'Composition'), ('selection-assignment', 'Selection Assignment')], db_index=True, default='composition'),
        ),
        migrations.AlterField(
            model_name='project',
            name='response_view_policy',
            field=models.TextField(choices=[('never', 'Never - Responses visible only to instructors'), ('always', 'Always - Response not required to view other student responses'), ('submitted', 'After Submission - Response required to view other student responses before due date. All responses visible after assignment due date passes.')], default='always'),
        ),
    ]