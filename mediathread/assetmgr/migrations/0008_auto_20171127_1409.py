# -*- coding: utf-8 -*-
# Generated by Django 1.11.7 on 2017-11-27 14:09
from __future__ import unicode_literals

from os.path import basename
from urlparse import urlparse, parse_qs
from django.db import migrations
from django.db.models import Q


def update_url(url):
    """Updates the given URL.

    There are a few formats I'm checking for. Details here:
    https://pmt.ccnmtl.columbia.edu/item/112484/#event-9
    """
    if url.startswith('http://www.blakearchive.org/blake/images/'):
        fname = basename(url)
        return 'http://www.blakearchive.org/images/{}'.format(fname)
    elif url.startswith('http://www.blakearchive.org/') and 'objectid=' in url:
        objectid = parse_qs(urlparse(url).query).get('objectid')[0]
        return 'http://www.blakearchive.org/copy/s-inn.b?descId={}'.format(
            objectid)

    return url


def update_blake_urls(apps, course):
    """Update the URLs in the given Course."""
    Asset = apps.get_model('assetmgr', 'Asset')
    for asset in Asset.objects.filter(course=course):
        for source in asset.source_set.all():
            new_url = update_url(source.url)
            if source.url != new_url:
                source.url = new_url
                source.save()


def update_all_blake_urls(apps, schema_editor):
    Course = apps.get_model('courseaffils', 'Course')
    courses = Course.objects.filter(
        Q(title='Multimedia Blake \'14') | Q(title='Multimedia Blake'))
    for course in courses:
        update_blake_urls(apps, course)


class Migration(migrations.Migration):

    dependencies = [
        ('assetmgr', '0007_auto_20160831_1438'),
    ]

    operations = [
        migrations.RunPython(update_all_blake_urls),
    ]
