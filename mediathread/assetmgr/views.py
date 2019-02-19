# pylint: disable-msg=C0302
import datetime
import hashlib
import hmac
import json
import re
import urllib
import urllib2
from urlparse import urlparse

from courseaffils.lib import in_course_or_404, in_course, AUTO_COURSE_SELECT
from courseaffils.models import CourseAccess
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db.models.aggregates import Max
from django.db.models.query_utils import Q
from django.http import HttpResponse, HttpResponseForbidden, \
    HttpResponseRedirect, Http404
from django.http.response import HttpResponseNotFound, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404
from django.template import loader
from django.views.generic.base import View, TemplateView
from djangohelpers.lib import allow_http

from mediathread.api import UserResource, TagResource
from mediathread.assetmgr.api import AssetResource
from mediathread.assetmgr.models import Asset, Source, ExternalCollection, \
    SuggestedExternalCollection
from mediathread.djangosherd.api import DiscussionIndexResource
from mediathread.djangosherd.models import SherdNote, DiscussionIndex
from mediathread.djangosherd.views import create_annotation, edit_annotation, \
    delete_annotation
from mediathread.main import course_details
from mediathread.main.models import UserSetting
from mediathread.main.util import log_sentry_error
from mediathread.mixins import ajax_required, LoggedInCourseMixin, \
    JSONResponseMixin, AjaxRequiredMixin, RestrictedMaterialsMixin, \
    LoggedInSuperuserMixin
from mediathread.taxonomy.api import VocabularyResource
from mediathread.taxonomy.models import Vocabulary


def _parse_domain(url):
    parsed_uri = urlparse(url)
    return '{uri.scheme}://{uri.netloc}/'.format(uri=parsed_uri)


class ManageExternalCollectionView(LoggedInCourseMixin, View):

    def post(self, request):
        suggested_id = request.POST.get('suggested_id', None)
        collection_id = request.POST.get('collection_id', None)
        if 'remove' in request.POST.keys():
            exc = get_object_or_404(ExternalCollection, id=collection_id)
            msg = '%s has been disabled for your class.' % exc.title
            exc.delete()
        elif suggested_id:
            suggested = get_object_or_404(SuggestedExternalCollection,
                                          id=suggested_id)
            exc = ExternalCollection()
            exc.title = suggested.title
            exc.url = suggested.url
            exc.thumb_url = suggested.thumb_url
            exc.description = suggested.description
            exc.course = request.course
            exc.save()
            msg = '%s has been enabled for your class.' % exc.title
        else:
            exc = ExternalCollection()
            exc.title = request.POST.get('title')
            exc.url = request.POST.get('url')
            exc.thumb_url = request.POST.get('thumb')
            exc.description = request.POST.get('description')
            exc.course = request.course
            exc.uploader = request.POST.get('uploader', False)
            exc.save()

            if exc.uploader:
                course_details.add_upload_folder(request.course)

            msg = '%s has been enabled for your class.' % exc.title

        messages.add_message(request, messages.INFO, msg)

        redirect_url = request.POST.get('redirect-url',
                                        reverse('class-manage-sources'))
        return HttpResponseRedirect(redirect_url)


@login_required
def asset_switch_course(request, asset_id):
    try:
        asset = Asset.objects.get(pk=asset_id)
        in_course_or_404(request.user.username, asset.course)

        # the user is logged into the wrong class?
        rv = {}
        rv['switch_to'] = asset.course
        rv['switch_from'] = request.course
        rv['redirect'] = reverse('asset-view', args=[asset_id])
        return render(request, 'assetmgr/asset_not_found.html', rv)
    except Asset.DoesNotExist:
        raise Http404("This item does not exist.")


def asset_workspace_courselookup(asset_id=None, annot_id=None):
    """lookup function corresponding to asset_workspace
    if an asset is being requested then we can guess the course
    """
    if asset_id:
        return Asset.objects.get(pk=asset_id).course


@login_required
@allow_http("GET", "POST")
def most_recent(request):
    user = request.user
    user_id = user.id
    asset = Asset.objects.filter(author_id=user_id).order_by('-modified')[0]
    asset_id = str(asset.id)
    return HttpResponseRedirect('/asset/' + asset_id + '/')


class AssetCreateView(View):
    OPERATION_TAGS = ('jump', 'title', 'noui', 'v', 'share',
                      'as', 'set_course', 'secret')

    @classmethod
    def good_asset_arg(cls, key):
        # need support for some things like width,height,max_zoom
        return (not (key.startswith('annotation-') or
                     key.startswith('save-') or
                     key.startswith('metadata-') or  # asset metadata
                     key.endswith('-metadata')  # source metadata
                     ) and
                key not in cls.OPERATION_TAGS)

    @classmethod
    def add_metadata(cls, source, src_metadata):
        if src_metadata:
            # w{width}h{height};{mimetype}
            # (with mimetype and w+h optional)
            the_match = re.match(r'(w(\d+)h(\d+))?(;(\w+/[\w+]+))?',
                                 src_metadata).groups()
            if the_match[1]:
                source.width = int(the_match[1])
                source.height = int(the_match[2])
            if the_match[4]:
                source.media_type = the_match[4]

    @classmethod
    def sources_from_args(cls, request, asset=None):
        '''
        utilized by add_view to help create a new asset
        returns a dict of sources represented in GET/POST args
        '''
        sources = {}
        args = request.POST if request.method == 'POST' else request.GET
        for key, val in args.items():
            if cls.good_asset_arg(key) and val != '' and len(val) < 4096:
                source = Source(label=key, url=val)

                # UGLY non-functional programming for url_processing
                if asset:
                    source.asset = asset

                src_metadata = args.get(key + '-metadata', None)
                cls.add_metadata(source, src_metadata)
                sources[key] = source

        # iterate the primary labels in order of priority
        # pickup the first matching one & use that
        for lbl in Asset.primary_labels:
            if lbl in sources:
                sources[lbl].primary = True
                return sources

        # no primary source found, return no sources
        return {}

    def add_sources(self, request, asset):
        for source in self.sources_from_args(request, asset).values():
            source.save()

    def add_tags(self, asset, user, metadata):
        if "tag" in metadata:
            for each_tag in metadata["tag"]:
                asset.save_tag(user, each_tag)

    def parse_user(self, request):
        user = request.user
        args = request.POST if request.method == 'POST' else request.GET
        if ((user.is_staff or CourseAccess.allowed(request)) and 'as' in args):
            as_user = args['as']
            user = get_object_or_404(User, username=as_user)

        return user

    def parse_metadata(self, req_dict):
        metadata = {}
        for key in req_dict:
            if key.startswith('metadata-'):
                metadata[key[len('metadata-'):]] = req_dict.getlist(key)
        return metadata

    ''' No login required so server2server interface is possible'''
    def post(self, request):
        user = self.parse_user(request)
        if not request.course or not request.course.is_member(user):
            return HttpResponseForbidden(
                "You must be a member of the course to add assets.")

        req_dict = getattr(request, request.method)

        metadata = self.parse_metadata(req_dict)
        title = req_dict.get('title', '')
        success, asset = Asset.objects.get_by_args(
            req_dict, asset__course=request.course)

        if success is False:
            log_sentry_error(
                'Asset creation failed with request data: ' + str(req_dict))
            return HttpResponseBadRequest(
                'The selected asset didn\'t have the correct data to be ' +
                'imported into Mediathread.')

        if asset is None:
            asset = Asset(title=title[:1020],  # max title length
                          course=request.course,
                          author=user)
            asset.save()

            self.add_sources(request, asset)

            self.add_tags(asset, user, metadata)

            asset.metadata_blob = json.dumps(metadata)
            asset.save()

            try:
                asset.primary  # make sure a primary source was specified
            except Source.DoesNotExist:
                # we'll make it here if someone doesn't submit
                # any primary_labels as arguments
                # @todo verify the above comment.
                raise AssertionError("no primary source provided")

        # create a global annotation
        asset.global_annotation(user, True)
        asset_url = reverse('asset-view', args=[asset.id])
        source = request.POST.get('asset-source', "")

        if source == 'bookmarklet':
            # bookmarklet create
            asset_url += "?level=item"

            template = loader.get_template('assetmgr/analyze.html')
            context = {
                'request': request,
                'user': user,
                'action': request.POST.get('button', None),
                'asset_url': asset_url
            }
            return HttpResponse(template.render(context))
        else:
            # server2server create (wardenclyffe)
            return HttpResponseRedirect(asset_url)


class AssetUpdateView(View):

    def get_asset_id(self, url):
        matches = re.findall(r'/(\d+)/', url)

        if len(matches) > 0:
            return int(matches[0])

        return None

    ''' No login required so server2server interface is possible'''
    ''' Authentication is managed via courseaffils CourseAccess '''
    ''' which validates a shared secret '''
    def post(self, request, *args, **kwargs):
        if not CourseAccess.allowed(request):
            return HttpResponseForbidden()

        try:
            arg = self.request.POST.get('asset-url', None)
            asset = Asset.objects.get(id=self.get_asset_id(arg))
        except (TypeError, Asset.DoesNotExist):
            return HttpResponseNotFound()

        for key, value in request.POST.items():
            # replace primary source completely. the labels will differ
            if key in Asset.primary_labels:
                if len(value) < 1:
                    # don't empty out a primary source
                    msg = 'Invalid primary source for {}'.format(key)
                    return HttpResponseBadRequest(msg)

                asset.update_primary(key, value)
            else:
                # update a secondary source based on label
                sources = asset.source_set.filter(label=key)
                sources.update(url=value)

        return HttpResponse('updated')


@login_required
@allow_http("GET", "POST")
@ajax_required
def asset_delete(request, asset_id):
    in_course_or_404(request.user.username, request.course)

    # Remove all annotations by this user
    # By removing the "global annotation" this effectively removes
    # The asset from the workspace
    asset = get_object_or_404(Asset, pk=asset_id, course=request.course)
    annotations = asset.sherdnote_set.filter(author=request.user)
    annotations.delete()

    json_stream = json.dumps({})
    return HttpResponse(json_stream, content_type='application/json')


@login_required
@allow_http("POST")
def annotation_create(request, asset_id):
    """
    delegate to djangosherd view and redirect back to asset workspace

    but first, stuff a range into the request
    and get the annotation context from the url
    """

    # Verify asset exists
    get_object_or_404(Asset, pk=asset_id, course=request.course)

    form = request.POST.copy()
    form['annotation-context_pk'] = asset_id
    request.POST = form

    form = request.GET.copy()
    form['annotation-next'] = reverse('asset-view', args=[asset_id])
    request.GET = form

    return create_annotation(request)


@login_required
@allow_http("POST")
@ajax_required
def annotation_create_global(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id, course=request.course)
    global_annotation = asset.global_annotation(request.user, True)

    response = {
        'asset': {'id': asset_id},
        'annotation': {
            'id': global_annotation.id,
            'creating': True
        }
    }
    return HttpResponse(json.dumps(response),
                        content_type="application/json")


@login_required
@allow_http("POST")
def annotation_save(request, asset_id, annot_id):
    try:
        # Verify annotation exists
        ann = SherdNote.objects.get(pk=annot_id,
                                    asset=asset_id,
                                    asset__course=request.course)

        if (ann.is_global_annotation() and
            'asset-title' in request.POST and
                (request.user.is_staff or request.user == ann.asset.author)):
            ann.asset.title = request.POST.get('asset-title')
            ann.asset.save()

        form = request.GET.copy()
        form['next'] = '.'
        request.GET = form
        return edit_annotation(request, annot_id)  # djangosherd.views
    except SherdNote.DoesNotExist:
        return HttpResponseForbidden("forbidden")


@login_required
@allow_http("POST")
def annotation_delete(request, asset_id, annot_id):
    try:
        # Verify annotation exists
        SherdNote.objects.get(pk=annot_id,
                              asset=asset_id,
                              asset__course=request.course)

        redirect_to = reverse('asset-view', args=[asset_id])

        form = request.GET.copy()
        form['next'] = redirect_to
        request.GET = form
        return delete_annotation(request, annot_id)  # djangosherd.views
    except SherdNote.DoesNotExist:
        return HttpResponseNotFound()


class AnnotationCopyView(LoggedInCourseMixin, AjaxRequiredMixin,
                         JSONResponseMixin, View):

    def post(self, request, *args, **kwargs):
        annot_id = kwargs.get('annot_id', None)
        note = get_object_or_404(SherdNote, pk=annot_id)

        # add this to the user's collection
        note.asset.global_annotation(request.user, True)

        data = {
            'author': self.request.user, 'asset': note.asset,
            'range1': note.range1, 'range2': note.range2,
            'annotation_data': note.annotation_data, 'title': note.title
        }

        new_note = SherdNote(**data)
        new_note.save()

        ctx = {'asset': {'id': new_note.asset.id},
               'annotation': {'id': new_note.id}}
        return self.render_to_json_response(ctx)


class RedirectToExternalCollectionView(LoggedInCourseMixin, View):
    """
        simple way to redirect to a stored (thus obfuscated) url
    """

    def get(self, request, collection_id):
        exc = get_object_or_404(ExternalCollection, id=collection_id)
        return HttpResponseRedirect(exc.url)


class RedirectToUploaderView(LoggedInCourseMixin, View):

    def get_upload_folder(self):
        return course_details.get_upload_folder(self.request.course)

    def post(self, request, *args, **kwargs):
        collection_id = kwargs['collection_id']
        exc = get_object_or_404(ExternalCollection, id=collection_id)

        special = getattr(settings, 'SERVER_ADMIN_SECRETKEYS', {})
        if exc.url not in special.keys():
            raise Http404("The uploader does not exist.")

        username = request.user.username
        as_user = request.POST.get('as', None)
        if (as_user and in_course(request.user.username, request.course) and
            (request.user.is_staff or
             request.user.has_perm('assetmgr.can_upload_for'))):
            username = as_user

        redirect_back = "%s?msg=upload" % (request.build_absolute_uri('/'))

        nonce = '%smthc' % datetime.datetime.now().isoformat()

        digest = hmac.new(special[exc.url],
                          '%s:%s:%s' % (username, redirect_back, nonce),
                          hashlib.sha1).hexdigest()

        url = ("%s?set_course=%s&as=%s&redirect_url=%s"
               "&nonce=%s&hmac=%s&audio=%s&folder=%s") % (
            exc.url, request.course.group.name, username,
            urllib.quote(redirect_back), nonce, digest,
            request.POST.get('audio', ''), self.get_upload_folder())

        return HttpResponseRedirect(url)


def final_cut_pro_xml(request, asset_id):
    if not request.user.is_staff:
        return HttpResponseForbidden()

    '''support for http://developer.apple.com/mac/library/documentation/ \
    AppleApplications/Reference/FinalCutPro_XML/Topics/Topics.html'''
    try:
        from xmeml import VideoSequence
        # http://github.com/ccnmtl/xmeml
        asset = get_object_or_404(Asset, pk=asset_id)

        xmeml = asset.sources.get('xmeml', None)
        if xmeml is None:
            return HttpResponse("Not Found: This annotation's asset does not \
            have a Final Cut Pro source XML associated with it", status=404)

        the_file = urllib2.urlopen(xmeml.url)  # nosec
        assert the_file.code == 200
        the_video = VideoSequence(xml_string=the_file.read())

        clips = []
        keys = request.POST.keys()
        keys.sort(key=lambda x: int(x))
        for key in keys:
            ann = asset.sherdnote_set.get(id=request.POST.get(key),
                                          range1__isnull=False)
            if ann:
                clip = the_video.clip(ann.range1, ann.range2, units='seconds')
                clips.append(clip)

        xmldom, dumb_uuid = the_video.clips2dom(clips)
        res = HttpResponse(xmldom.toxml(), content_type='application/xml')
        res['Content-Disposition'] = \
            'attachment; filename="%s.xml"' % asset.title
        return res

    except ImportError:
        return HttpResponse('Not Implemented: No Final Cut Pro Xmeml support',
                            status=503)


class ScalarExportView(LoggedInSuperuserMixin, RestrictedMaterialsMixin, View):

    def __init__(self):
        self.export = {}
        self.tag_num = 0
        self.anno_num = 0
        self.root = ''

    def get_time_string(self, root, data, n):
        time = ''
        try:
            note = data['annotations'][n]['annotation']
            if note:
                time += root
                time += data.get('local_url').rstrip('/')
                time += '#t=npt:'
                time += str(note['start'])
                time += ','
                time += str(note['end'])
        except KeyError:
            pass

        return time

    def parse_annotations(self, data, n):
        user_node = {}
        username = data['annotations'][n]['author']['username']
        author_user = User.objects.get(username=username)
        author_email = author_user.email
        hash_or_username = hashlib.sha1(author_email).hexdigest()

        user_node['http://xmlns.com/foaf/0.1/name'] = [{"value": data.get(
            'annotations')[n]['author']['public_name'], "type": "literal"}]
        user_node['http://www.w3.org/1999/02/22-rdf-syntax-ns#type'] = [
            {"value": "http://xmlns.com/foaf/0.1/Person", "type": "uri"}]
        user_node['http://xmlns.com/foaf/0.1/mbox_sha1sum'] = [
            {"value": hash_or_username, "type": "literal"}]
        self.export[self.root + '/user/' + hash_or_username] = user_node

        annotation_node = {}
        annotation_node['http://purl.org/dc/terms/title'] = [
            {"value": data['annotations'][n]['title'],
             "type": "literal"}]
        annotation_node['http://purl.org/dc/terms/description'] = [
            {"value": "This is an annotation", "type": "literal"}]
        annotation_node['http://rdfs.org/sioc/ns#content'] = [
            {"value": data['annotations'][n]['metadata']['body'],
             "type": "literal"}]
        annotation_node['http://www.w3.org/ns/prov#wasAttributedTo'] = [
            {"value": self.root +
             data['annotations'][n]['author']['resource_uri'].rstrip('/') +
             '/user/' + hash_or_username,
             "type": "uri"}]
        a_annotation_node = {}
        a_annotation_node['http://www.openannotation.org/ns/hasBody'] = [
            {"value": self.root +
             data['annotations'][n]['url'].rstrip('/'),
             "type": "uri"}]

        time = self.get_time_string(self.root, data, n)
        a_annotation_node['http://www.openannotation.org/ns/hasTarget'] = [
            {"value": time, "type": "uri"}]

        a_annotation_node[
            'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
        ] = [
            {"value": "http://www.openannotation.org/ns/Annotation",
             "type": "uri"}]
        anno_urn = 'urn:mediathread:anno' + str(self.anno_num + 1)
        self.export[anno_urn.rstrip('/')] = a_annotation_node
        self.anno_num += 1

        tag = (data['annotations'][n]['metadata']['tags'])
        for k in range(0, len(tag)):
            self.parse_tag(data, k, tag, n)

        vocab = data['annotations'][n]['vocabulary']
        self.parse_vocab(vocab, n, data)

        self.export[self.root + data['annotations'][n]
                    ['url'].rstrip('/')] = annotation_node

    def parse_vocab(self, vocab, n, data):
        for j in range(0, len(vocab)):
            num = 0
            for t in vocab[j]['terms']:
                num += 1
                urn_vocab_node = {}
                urn_vocab_node[
                    'http://www.openannotation.org/ns/hasBody'] = [
                    {"value": self.root +
                        '/term/' + vocab[j]['display_name'] +
                        '-' + t['name'],
                     "type": "literal"}]
                urn_vocab_node[
                    'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
                ] = [{"value":
                      "http://www.openannotation.org/ns/Annotation",
                      "type": "uri"}]

                term_node = {}
                term_node[
                    'http://www.w3.org/1999/02/22' +
                    '-rdf-syntax-ns#Description'
                ] = [
                    {"value": vocab[j]['display_name'] +
                        '-' + t['name'], "type": "literal"}]
                term_node[
                    'http://www.w3.org/2000/01/rdf-schema#label'
                ] = [
                    {"value": vocab[j]['display_name'] +
                        '-' + t['name'], "type": "literal"}]

                if t['skos_uri'] is not None:
                    term_node[
                        'http://www.w3.org/2004/02/skos/core#related'
                    ] = [{"value": t['skos_uri'].rstrip('/'),
                         "type": "uri"}]

                term_node[
                    'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
                ] = [
                    {"type": "uri",
                     "value":
                        "http://www.openannotation.org/ns/SemanticTag"}
                    ]
                self.export[self.root + '/term/' + vocab[j]
                            ['display_name'] + '-' +
                            t['name']] = term_node

                urn_vocab_node[
                    'http://www.openannotation.org/ns/hasTarget'
                ] = [
                    {"value": self.root +
                        data.get(
                            'annotations')[n]['url'].rstrip('/'),
                     "type": "uri"}]

                self.export["urn:mediathread:term" +
                            str(num)] = urn_vocab_node

    def parse_tag(self, data, k, tag, n):
        tag_node = {}
        tag_node["http://www.w3.org/2000/01/rdf-schema#label"] = [
            {"value": tag[k]['name'], "type": "literal"}]
        self.export[self.root + '/tag/' + tag[k]['name']] = tag_node
        a_tag_node = {}

        a_tag_node[
            'http://www.openannotation.org/ns/hasTarget'] = [
            {"value": self.root + data.get(
             'annotations')[n]['url'].rstrip('/'),
             "type": "uri"}]

        if (len(tag[k]['resource_uri']) > 1):
            a_tag_node[
                'http://www.openannotation.org/ns/hasBody'
            ] = [
                {
                    "value": self.root + '/tag/' + tag[k][
                        'resource_uri'].rstrip('/'), "type": "uri"}
                ]
        else:
            a_tag_node[
                'http://www.openannotation.org/ns/hasBody'] = [
                {"value": self.root +
                 '/tag/' + tag[k]['name'].rstrip('/'),
                 "type": "uri"}]

        a_tag_node[
            'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'] = [
            {"value": "http://www.openannotation.org/ns/Annotation",
             "type": "uri"}]
        self.tag_num += 1
        a_tag_urn = 'urn:mediathread:tag' + str(self.tag_num)
        self.export[a_tag_urn] = a_tag_node

    def parse_response(self, data):
        video_node = {}
        # for the video node
        video_node[
            'http://purl.org/dc/terms/title'
            ] = [{"value": data.get('title'), "type": "literal"}]
        video_node['http://purl.org/dc/terms/description'] = [
            {"value": data.get('description'), "type": "literal"}]
        video_node['http://simile.mit.edu/2003/10/ontologies/artstor#url'] = [
            {"value": data.get('sources')[
             data.get('primary_type')]['url'], "type": "uri"}]
        video_node['http://purl.org/dc/terms/source'] = [
            {"value": data.get('primary_type'), "type": "literal"}]
        video_node['http://purl.org/dc/terms/date'] = [
            {"value": data.get('modified'), "type": "literal"}]
        video_node['http://purl.org/dc/terms/contributor'] = [
            {"value": data.get('author')['username'], "type": "literal"}]
        self.export[self.root + data.get('local_url').rstrip('/')] = video_node
        # for annotation node
        for n in range(0, data.get('annotation_count')):
            self.parse_annotations(data, n)

    def get(self, request):
        self.root = 'http://' + request.get_host()
        assets = Asset.objects.filter(course=request.course)
        assets, notes = self.visible_assets_and_notes(request, assets)

        # @todo - factor out the tastypie interim json step
        ar = AssetResource(include_annotations=True)
        api_response = ar.render_list(request, None, request.user,
                                      assets, notes)

        if len(api_response) == 0:
            return HttpResponse("There are no videos in your collection")

        for i in range(0, len(api_response)):
            self.parse_response(api_response[i])

        return HttpResponse(json.dumps(self.export))


class AssetReferenceView(LoggedInCourseMixin, RestrictedMaterialsMixin,
                         AjaxRequiredMixin, JSONResponseMixin, View):

    def get(self, request, asset_id):
        ctx = {}
        qs = Asset.objects.filter(pk=asset_id, course=request.course)

        if qs.count() > 0:
            notes = SherdNote.objects.get_related_notes(
                qs, self.record_owner, self.visible_authors,
                self.all_items_are_visible)

            # tags
            ctx['tags'] = TagResource().render_related(request, notes)

            # vocabulary
            ctx['vocabulary'] = VocabularyResource().render_related(request,
                                                                    notes)

            # DiscussionIndex is misleading. Objects returned are
            # projects & discussions title, object_pk, content_type, modified
            indicies = DiscussionIndex.objects.filter(
                asset=qs[0]).order_by('-modified')
            ctx.update(DiscussionIndexResource().render_list(request,
                                                             indicies))
        return self.render_to_json_response(ctx)


EMBED_WIDTH = 635
EMBED_HEIGHT = 450


class AssetEmbedListView(LoggedInCourseMixin, RestrictedMaterialsMixin,
                         TemplateView):

    template_name = 'assetmgr/asset_embed_list.html'

    def get(self, request):
        user_resource = UserResource()
        owners = user_resource.render_list(request, request.course.members)

        data = {
            'owners': json.dumps(owners),
            'return_url': request.GET.get('return_url', ''),
            'width': EMBED_WIDTH,
            'height': EMBED_HEIGHT
        }

        return self.render_to_response(data)

    def get_selection(self, keys, user):
        for k in keys:
            if k.startswith('item-'):
                item_id = k.split('-')[1]
                item = get_object_or_404(Asset, pk=item_id)
                return item.global_annotation(user, True)

            if k.startswith('selection-'):
                selection_id = k.split('-')[1]
                selection = get_object_or_404(SherdNote, pk=selection_id)
                return selection

        return None

    def get_dimensions(self, source):
        return {'width': EMBED_WIDTH, 'height': EMBED_HEIGHT}

    def get_secret(self, return_url):
        return_domain = _parse_domain(return_url)

        special = getattr(settings, 'SERVER_ADMIN_SECRETKEYS', {})
        if return_domain not in special.keys():
            raise Http404("The domain is not recognized.")

        return special[return_domain]

    def get_iframe_url(self, secret, selection):
        view_url = reverse('selection-embed-view',
                           kwargs={'course_id': self.request.course.id,
                                   'annot_id': selection.id})

        nonce = '%smthc' % datetime.datetime.now().isoformat()
        digest = hmac.new(
            secret,
            '%s:%s:%s' % (self.request.course.id, selection.id, nonce),
            hashlib.sha1).hexdigest()

        iframe_url = '%s://%s%s?nonce=%s&hmac=%s' % (
            self.request.scheme, self.request.get_host(),
            view_url, nonce, digest)
        return urllib.quote(iframe_url, safe='~()*!.\'')

    def post(self, request):
        return_url = request.POST.get('return_url', '')
        if len(return_url) == 0:
            raise Http404("The domain is not recognized")

        secret = self.get_secret(return_url)

        selection = self.get_selection(self.request.POST.keys(),
                                       self.request.user)

        iframe_url = self.get_iframe_url(secret, selection)

        dims = self.get_dimensions(selection.asset.primary)

        url = '{}?return_type=iframe&width={}&height={}&url={}'.format(
            return_url, dims['width'], dims['height'], iframe_url)

        return HttpResponseRedirect(url)


class AssetEmbedView(TemplateView):
    template_name = 'assetmgr/asset_embed_view.html'

    def check_signature(self, course_id, selection_id):
        special = getattr(settings, 'SERVER_ADMIN_SECRETKEYS', {})

        try:
            # get the domain from the referer
            referer = _parse_domain(self.request.META['HTTP_REFERER'])
        except KeyError:
            referer = settings.DEFAULT_LTI_CONSUMER

        if referer not in special.keys():
            return False
        secret = special[referer]

        nonce = self.request.GET.get('nonce')
        digest = self.request.GET.get('hmac')

        new_digest = hmac.new(
            secret,
            '%s:%s:%s' % (course_id, selection_id, nonce),
            hashlib.sha1).hexdigest()

        return digest == new_digest

    def get_context_data(self, **kwargs):
        course_id = kwargs.get('course_id')
        selection_id = kwargs.get('annot_id', None)

        if not self.check_signature(course_id, selection_id):
            raise Http404()

        selection = get_object_or_404(SherdNote, pk=selection_id)
        ctx = AssetResource().render_one_context(
            self.request, selection.asset, [selection])

        media_type = selection.asset.media_type()

        ctx = {'item': json.dumps(ctx),
               'item_id': selection.asset.id,
               'selection_id': selection.id,
               'presentation': 'medium',
               'media_type': media_type,
               'title': selection.display_title(),
               'thumb_url': selection.asset.thumb_url,
               'defaultHeight': EMBED_HEIGHT}

        if media_type == 'video':
            ctx['timecode'] = selection.range_as_timecode()

        return ctx


class AssetWorkspaceView(LoggedInCourseMixin, RestrictedMaterialsMixin,
                         JSONResponseMixin, View):

    def get(self, request, asset_id=None, annot_id=None):
        if asset_id:
            try:
                asset = Asset.objects.get(pk=asset_id, course=request.course)
                asset.primary
            except Asset.DoesNotExist:
                return asset_switch_course(request, asset_id)
            except Source.DoesNotExist:
                return render(request, '500.html', {})

        ctx = {'asset_id': asset_id, 'annotation_id': annot_id}

        if not request.is_ajax():
            return render(request, 'assetmgr/asset_workspace.html', ctx)

        qs = Vocabulary.objects.filter(course=request.course)
        vocabulary = VocabularyResource().render_list(request, qs)

        user_resource = UserResource()
        owners = user_resource.render_list(request, request.course.members)

        ctx['panels'] = [{
            'panel_state': 'open',
            'panel_state_label': "Annotate Media",
            'context': {
                'type': 'asset',
                'is_faculty': self.is_viewer_faculty,
                'allow_item_download': self.is_viewer_faculty and
                course_details.allow_item_download(request.course)
            },
            'owners': owners,
            'vocabulary': vocabulary,
            'template': 'asset_workspace',
            'current_asset': asset_id,
            'current_annotation': annot_id,
            'update_history': True,
            'show_collection': True}]

        return self.render_to_json_response(ctx)


class AssetDetailView(LoggedInCourseMixin, RestrictedMaterialsMixin,
                      JSONResponseMixin, View):

    def get(self, request, asset_id):
        the_assets = Asset.objects.filter(pk=asset_id, course=request.course)
        if the_assets.count() == 0:
            return asset_switch_course(request, asset_id)

        (assets, notes) = self.visible_assets_and_notes(request, the_assets)

        # if asset is not in my collection, it must be in my course
        if assets.count() == 0 and the_assets[0].course != request.course:
            return HttpResponseForbidden("forbidden")

        # only return original author's global annotations
        notes = notes.exclude(~Q(author=request.user), range1__isnull=True)

        asset = the_assets[0]
        if asset.primary.is_image():
            notes = notes.order_by('author', 'title')
        else:
            notes = notes.order_by('author', 'range1', 'title')

        notes = notes.prefetch_related(
            'termrelationship_set__term__vocabulary',
            'projectnote_set__project')

        help_setting = UserSetting.get_setting(request.user,
                                               "help_item_detail_view",
                                               True)

        ctx = AssetResource().render_one_context(request, asset, notes)
        ctx['user_settings'] = {'help_item_detail_view': help_setting}
        ctx['type'] = 'asset'

        return self.render_to_json_response(ctx)


class AssetCollectionView(LoggedInCourseMixin, RestrictedMaterialsMixin,
                          JSONResponseMixin, View):
    """
    An ajax-only request to retrieve assets for a course or a specified user
    Example:
        /api/asset/user/sld2131/
        /api/asset/
    """
    valid_filters = ['tag', 'modified', 'search_text',
                     'media_type', 'primary_type']

    def get_context(self, request, assets, notes):
        # Allow the logged in user to add assets to his composition
        citable = request.GET.get('citable', '') == 'true'

        # Include annotation metadata. (makes response much larger)
        include_annotations = request.GET.get('annotations', '') == 'true'

        # Initialize the context
        ures = UserResource()
        ctx = {
            'space_viewer': ures.render_one(request, self.record_viewer),
            'is_faculty': self.is_viewer_faculty
        }

        if self.record_owner:
            ctx['space_owner'] = ures.render_one(request, self.record_owner)

        ctx['active_filters'] = {}
        for key, val in request.GET.items():
            if (key in self.valid_filters or key.startswith('vocabulary-')):
                ctx['active_filters'][key] = val

        filtered_types = request.GET.getlist('primary_type[]')
        if filtered_types:
            ctx['active_filters']['primary_types'] = filtered_types

        ctx['editable'] = self.viewing_own_records
        ctx['citable'] = citable

        # render the assets
        ares = AssetResource(include_annotations=include_annotations,
                             extras={'editable': self.viewing_own_records,
                                     'citable': citable})
        ctx['assets'] = ares.render_list(request,
                                         self.record_owner,
                                         self.record_viewer,
                                         assets, notes)

        return ctx

    def add_metadata(self, request, assets):
        # metadata for all notes associated with these assets
        # is displayed in the filtered list.
        notes = SherdNote.objects.get_related_notes(
            assets, self.record_owner or None, self.visible_authors,
            self.all_items_are_visible)

        tags = TagResource().render_for_course(request, notes)
        vocab = VocabularyResource().render_for_course(request, notes)
        return {'active_tags': tags, 'active_vocabulary': vocab}

    def apply_pagination(self, assets, notes, offset, limit):
        # sort the object list via aggregation on the visible notes
        assets = notes.values('asset')
        assets = assets.annotate(last_modified=Max('modified'))
        assets = assets.order_by('-last_modified')

        # slice the list
        assets = assets[offset:offset + limit]
        ids = [a['asset'] for a in assets]
        return (assets, notes.filter(asset__id__in=ids))

    def get(self, request):
        if (self.record_owner):
            assets = Asset.objects.by_course_and_user(request.course,
                                                      self.record_owner)
        else:
            assets = Asset.objects.by_course(request.course)

        (assets, notes) = self.visible_assets_and_notes(request, assets)

        offset = int(request.GET.get("offset", 0))
        limit = int(request.GET.get("limit", 20))

        ctx = {}
        if offset == 0:
            # add relevant tags & metadata for all visible notes
            # needs to come before the pagination step
            ctx.update(self.add_metadata(request, assets))

        # slice down the list to speed rendering
        (assets, notes) = self.apply_pagination(assets, notes, offset, limit)

        # assemble the context
        ctx.update(self.get_context(request, assets, notes))

        return self.render_to_json_response(ctx)


AUTO_COURSE_SELECT[AssetWorkspaceView.as_view()] = asset_workspace_courselookup


class TagCollectionView(LoggedInCourseMixin, RestrictedMaterialsMixin,
                        AjaxRequiredMixin, JSONResponseMixin, View):

    def get(self, request):
        # Retrieve tags for this course
        assets = Asset.objects.filter(course=request.course)

        notes = SherdNote.objects.get_related_notes(
            assets, self.record_owner or None, self.visible_authors,
            self.all_items_are_visible)

        context = {}
        if len(notes) > 0:
            context = {'tags': TagResource().render_related(request, notes)}
        return self.render_to_json_response(context)


class BookmarkletMigrationView(TemplateView):
    template_name = 'assetmgr/bookmarklet_migration.html'
