# api.py
"""
Additions to MyTardis's REST API
"""
import logging
import traceback
from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core import mail
from django.core.mail import get_connection
from django.db import IntegrityError
from django.http import HttpResponse
from django.template import Context
from tastypie import fields
from tastypie.constants import ALL_WITH_RELATIONS
from tastypie.exceptions import ImmediateHttpResponse
from ipware.ip import get_ip

import tardis.tardis_portal.api
from tardis.tardis_portal.auth.decorators import has_datafile_access
from tardis.tardis_portal.models.facility import facilities_managed_by
from tardis.tardis_portal.models.experiment import Experiment
from tardis.tardis_portal.models.parameters import Schema
from tardis.tardis_portal.models.parameters import ParameterName
from tardis.tardis_portal.models.parameters import ExperimentParameter
from tardis.tardis_portal.models.parameters import ExperimentParameterSet
from tardis.tardis_portal.models.datafile import DataFileObject

from models.uploader import Uploader
from models.uploader import UploaderRegistrationRequest
from models.uploader import UploaderSetting

logger = logging.getLogger(__name__)


class ACLAuthorization(tardis.tardis_portal.api.ACLAuthorization):
    '''Authorisation class for Tastypie.
    '''
    def read_list(self, object_list, bundle):  # noqa # too complex
        authuser = bundle.request.user
        authenticated = authuser.is_authenticated()
        is_facility_manager = authenticated and \
            len(facilities_managed_by(authuser)) > 0
        if isinstance(bundle.obj, Uploader):
            if is_facility_manager:
                return object_list
            return []
        elif isinstance(bundle.obj, UploaderSetting):
            if is_facility_manager:
                return object_list
            return []
        elif isinstance(bundle.obj, UploaderRegistrationRequest):
            if is_facility_manager:
                return object_list
            return []
        else:
            return super(ACLAuthorization, self).read_list(object_list, bundle)

    def read_detail(self, object_list, bundle):  # noqa # too complex
        if bundle.request.user.is_authenticated() and \
           bundle.request.user.is_superuser:
            return True
        authuser = bundle.request.user
        authenticated = authuser.is_authenticated()
        is_facility_manager = authenticated and \
            len(facilities_managed_by(authuser)) > 0
        if isinstance(bundle.obj, Uploader):
            return is_facility_manager
        elif isinstance(bundle.obj, UploaderRegistrationRequest):
            return is_facility_manager
        elif isinstance(bundle.obj, DataFileObject):
            return has_datafile_access(bundle.request, bundle.obj.datafile.id)
        else:
            return super(ACLAuthorization, self).read_detail(object_list,
                                                             bundle)

    def create_list(self, object_list, bundle):
        return super(ACLAuthorization, self).create_list(object_list, bundle)

    def create_detail(self, object_list, bundle):
        authuser = bundle.request.user
        authenticated = authuser.is_authenticated()
        is_facility_manager = authenticated and \
            len(facilities_managed_by(authuser)) > 0
        if isinstance(bundle.obj, Uploader):
            return is_facility_manager
        elif isinstance(bundle.obj, UploaderRegistrationRequest):
            return is_facility_manager
        elif isinstance(bundle.obj, UploaderSetting):
            return is_facility_manager
        return super(ACLAuthorization, self).create_detail(object_list, bundle)

    def update_list(self, object_list, bundle):
        return super(ACLAuthorization, self).update_list(object_list, bundle)

    def update_detail(self, object_list, bundle):
        '''
        Uploaders should only be able to update the uploader record whose
        UUID matches theirs (if it exists).
        '''
        authuser = bundle.request.user
        authenticated = authuser.is_authenticated()
        is_facility_manager = authenticated and \
            len(facilities_managed_by(authuser)) > 0
        if isinstance(bundle.obj, Uploader):
            return is_facility_manager and \
                bundle.data['uuid'] == bundle.obj.uuid
        elif isinstance(bundle.obj, UploaderSetting):
            return is_facility_manager
        return super(ACLAuthorization, self).update_detail(object_list, bundle)

    def delete_list(self, object_list, bundle):
        return super(ACLAuthorization, self).delete_list(object_list, bundle)

    def delete_detail(self, object_list, bundle):
        return super(ACLAuthorization, self).delete_detail(object_list, bundle)


class UploaderAppResource(tardis.tardis_portal.api.MyTardisModelResource):
    instruments = \
        fields.ManyToManyField(tardis.tardis_portal.api.InstrumentResource,
                               'instruments', null=True, full=True)
    settings = fields.ToManyField(
        'tardis.apps.mydata.api.UploaderSettingAppResource',
        'settings',
        related_name='uploader',
        full=True, null=True)

    class Meta(tardis.tardis_portal.api.MyTardisModelResource.Meta):
        resource_name = 'uploader'
        authentication = tardis.tardis_portal.api.default_authentication
        authorization = ACLAuthorization()
        queryset = Uploader.objects.all()
        filtering = {
            'uuid': ('exact', ),
            'name': ('exact', ),
        }
        always_return_data = True

    def dehydrate(self, bundle):
        '''
        We want to be able to upload some fields to give MyTardis sys admins
        info about the client machine MyData is running on, but we don't
        want those fields to be available for download, so we remove them
        here.
        '''
        accessible_keys = ['id', 'resource_uri', 'name', 'settings',
                           'settings_updated', 'settings_downloaded']
        for key in bundle.data.keys():
            if key not in accessible_keys:
                del(bundle.data[key])
        return bundle

    def hydrate_m2m(self, bundle):
        '''
        Allow updating multiple UploaderSettings simultaneously.
        '''
        if getattr(bundle.obj, 'id', False) and 'settings' in bundle.data:
            uploader = bundle.obj
            for setting in bundle.data['settings']:
                try:
                    uploader_setting = \
                        UploaderSetting.objects.get(uploader=uploader,
                                                    key=setting['key'])
                    uploader_setting.value = setting['value']
                except UploaderSetting.DoesNotExist:
                    uploader_setting = UploaderSetting(uploader=uploader,
                                                       key=setting['key'],
                                                       value=setting['value'])
                uploader_setting.save()
            del(bundle.data['settings'])
            bundle.obj.settings_updated = datetime.now()
            bundle.obj.save()

        return super(UploaderAppResource, self).hydrate_m2m(bundle)

    def obj_create(self, bundle, **kwargs):
        bundle.data['created_time'] = datetime.now()
        bundle.data['updated_time'] = datetime.now()
        ip = get_ip(bundle.request)
        if ip is not None:
            bundle.data['wan_ip_address'] = ip
        bundle = super(UploaderAppResource, self).obj_create(bundle, **kwargs)
        return bundle

    def obj_update(self, bundle, **kwargs):
        # Workaround for
        # https://github.com/toastdriven/django-tastypie/issues/390 :
        if hasattr(bundle, "obj_update_done"):
            return
        bundle.data['updated_time'] = datetime.now()
        ip = get_ip(bundle.request)
        if ip is not None:
            bundle.data['wan_ip_address'] = ip
        bundle = super(UploaderAppResource, self).obj_update(bundle, **kwargs)
        bundle.obj_update_done = True
        return bundle


class UploaderRegistrationRequestAppResource(tardis.tardis_portal.api
                                             .MyTardisModelResource):
    uploader = fields.ForeignKey(
        'tardis.apps.mydata.api.UploaderAppResource', 'uploader')
    approved_storage_box = fields.ForeignKey(
        'tardis.tardis_portal.api.StorageBoxResource',
        'approved_storage_box', null=True, full=True)

    class Meta(tardis.tardis_portal.api.MyTardisModelResource.Meta):
        resource_name = 'uploaderregistrationrequest'
        authentication = tardis.tardis_portal.api.default_authentication
        authorization = ACLAuthorization()
        queryset = UploaderRegistrationRequest.objects.all()
        filtering = {
            'id': ('exact', ),
            'approved': ('exact', ),
            'requester_key_fingerprint': ('exact', ),
            'uploader': ALL_WITH_RELATIONS,
            'approved_storage_box': ALL_WITH_RELATIONS,
        }
        always_return_data = True

    def obj_create(self, bundle, **kwargs):
        bundle = super(UploaderRegistrationRequestAppResource, self)\
            .obj_create(bundle, **kwargs)
        try:
            site = Site.objects.get_current().domain
            subject = '[MyTardis] Uploader Registration Request Created'
            message = \
                "Hi, this message is for MyTardis Admins.\n\n" \
                "An uploader registration request has just been created:\n\n" \
                "%s/admin/mydata/uploaderregistrationrequest/%d\n\n" \
                "Thanks,\n" \
                "MyTardis\n" \
                % (site, bundle.obj.id)
            logger.info('Informing admins of a new '
                        'uploader registraion request.')
            mail.mail_admins(subject, message,
                             connection=get_connection(fail_silently=True))
        except:
            logger.error(traceback.format_exc())

        return bundle

    def hydrate(self, bundle):
        bundle = super(UploaderRegistrationRequestAppResource, self)\
            .hydrate(bundle)
        bundle.data['request_time'] = datetime.now()
        return bundle

    def save_related(self, bundle):
        if not hasattr(bundle.obj, 'approved_storage_box'):
            bundle.obj.approved_storage_box = None
        super(UploaderRegistrationRequestAppResource,
              self).save_related(bundle)


class UploaderSettingAppResource(tardis.tardis_portal.api.MyTardisModelResource):
    uploader = fields.ForeignKey(
        'tardis.apps.mydata.api.UploaderAppResource',
        'uploader',
        related_name='settings',
        full=False)

    class Meta(tardis.tardis_portal.api.MyTardisModelResource.Meta):
        resource_name = 'uploadersetting'
        authentication = tardis.tardis_portal.api.default_authentication
        authorization = ACLAuthorization()
        queryset = UploaderSetting.objects.all()
        always_return_data = True


class ExperimentAppResource(tardis.tardis_portal.api.ExperimentResource):
    '''Extends MyTardis's API for Experiments
    to allow querying of metadata relevant to MyData
    '''

    class Meta(tardis.tardis_portal.api.ExperimentResource.Meta):
        # This will be mapped to mydata_experiment by MyTardis's urls.py:
        resource_name = 'experiment'

    def obj_get_list(self, bundle, **kwargs):
        '''
        Used by MyData to determine whether an appropriate default experiment
        exists to add a dataset to.
        '''

        '''
        For backwards compatibility with older MyData versions, let's
        try to guess the folder structure if it wasn't provided:
        '''
        folder_structure = None
        if hasattr(bundle.request, 'GET') and \
                'folder_structure' not in bundle.request.GET:
            if 'group_folder_name' in bundle.request.GET and \
                    bundle.request.GET['group_folder_name'].strip() != '':
                folder_structure = 'User Group / ...'
            elif 'user_folder_name' in bundle.request.GET:
                if '@' in bundle.request.GET['user_folder_name']:
                    folder_structure = 'Email / ...'
                else:
                    folder_structure = 'Username / ...'
            else:
                folder_structure = 'Username / ...'

        '''
        Responds to title/folder_structure/[user_folder_name|group_folder_name]
        query for MyData.  This can be used by MyData to retrieve an experiment
        which can be used to collect datasets from multiple MyData instances.
        '''
        if hasattr(bundle.request, 'GET') and \
                'title' in bundle.request.GET and \
                ('user_folder_name' in bundle.request.GET or
                 'group_folder_name' in bundle.request.GET):

            title = bundle.request.GET['title']
            if 'folder_structure' in bundle.request.GET:
                folder_structure = bundle.request.GET['folder_structure']
            need_to_match_user = (folder_structure.startswith('Username /') or
                                  folder_structure.startswith('Email /'))
            need_to_match_group = folder_structure.startswith('User Group /')

            class UnknownUser(object):
                def __init__(self, username='UNKNOWN', email='UNKNOWN'):
                    self.username = username
                    self.email = email

            if need_to_match_user:
                user_folder_name = bundle.request.GET['user_folder_name']
                if folder_structure.startswith('Username /'):
                    try:
                        user_to_match = \
                            User.objects.get(username=user_folder_name)
                    except User.DoesNotExist:
                        user_to_match = UnknownUser(username=user_folder_name)
                elif folder_structure.startswith('Email /'):
                    try:
                        user_to_match = \
                            User.objects.get(email__iexact=user_folder_name)
                    except User.DoesNotExist:
                        user_to_match = UnknownUser(email=user_folder_name)

            if need_to_match_group:
                group_folder_name = bundle.request.GET['group_folder_name']

            mydata_default_exp_schema = Schema.objects.get(
                namespace='http://mytardis.org'
                '/schemas/mydata/defaultexperiment')

            exp_psets = ExperimentParameterSet.objects\
                .filter(experiment__title=title,
                        schema=mydata_default_exp_schema)
            for exp_pset in exp_psets:
                exp_params = ExperimentParameter.objects\
                    .filter(parameterset=exp_pset)
                matched_user = False
                matched_group = False
                for exp_param in exp_params:
                    if need_to_match_user and \
                            exp_param.name.name == 'user_folder_name' and \
                            (exp_param.string_value.lower() ==
                             user_to_match.username.lower() or
                             exp_param.string_value.lower() ==
                             user_to_match.email.lower()):
                        matched_user = True
                    if need_to_match_group and \
                            exp_param.name.name == 'group_folder_name' and \
                            exp_param.string_value == group_folder_name:
                        matched_group = True
                if (need_to_match_user and matched_user) or \
                        (need_to_match_group and matched_group) or \
                        (not need_to_match_user and not need_to_match_group):
                    experiment_id = exp_pset.experiment.id
                    exp_list = Experiment.objects.filter(pk=experiment_id)
                    if exp_list[0] in Experiment.safe.all(bundle.request.user):
                        return exp_list

            return []

        '''
        Responds to
        uploader/folder_structure/[user_folder_name|group_folder_name]
        query for MyData.  Each MyData instance generates a UUID the first time
        it runs on each upload PC. The UUID together with the user folder name
        (or group folder name) can be used to uniquely identify one particular
        user (or group) who has saved data on an instrument PC running a MyData
        instance identified by the UUID.
        '''
        if hasattr(bundle.request, 'GET') and \
                'uploader' in bundle.request.GET and \
                ('user_folder_name' in bundle.request.GET or
                 'group_folder_name' in bundle.request.GET):

            uploader_uuid = bundle.request.GET['uploader']
            if 'folder_structure' in bundle.request.GET:
                folder_structure = bundle.request.GET['folder_structure']
            need_to_match_user = (folder_structure.startswith('Username /') or
                                  folder_structure.startswith('Email /'))
            need_to_match_group = folder_structure.startswith('User Group /')

            if need_to_match_user:
                user_folder_name = bundle.request.GET['user_folder_name']
                if folder_structure.startswith('Username /'):
                    try:
                        user_to_match = \
                            User.objects.get(username=user_folder_name)
                    except User.DoesNotExist:
                        user_to_match = UnknownUser(username=user_folder_name)
                elif folder_structure.startswith('Email /'):
                    try:
                        user_to_match = \
                            User.objects.get(email__iexact=user_folder_name)
                    except User.DoesNotExist:
                        user_to_match = UnknownUser(email=user_folder_name)

            if need_to_match_group:
                group_folder_name = bundle.request.GET['group_folder_name']

            mydata_default_exp_schema = Schema.objects.get(
                namespace='http://mytardis.org'
                '/schemas/mydata/defaultexperiment')

            exp_psets = ExperimentParameterSet.objects\
                .filter(schema=mydata_default_exp_schema)
            for exp_pset in exp_psets:
                exp_params = ExperimentParameter.objects\
                    .filter(parameterset=exp_pset)
                matched_uploader_uuid = False
                matched_user = False
                matched_group = False
                for exp_param in exp_params:
                    if exp_param.name.name == 'uploader' and \
                            exp_param.string_value == uploader_uuid:
                        matched_uploader_uuid = True
                    if need_to_match_user and \
                            exp_param.name.name == 'user_folder_name' and \
                            (exp_param.string_value.lower() ==
                             user_to_match.username.lower() or
                             exp_param.string_value.lower() ==
                             user_to_match.email.lower()):
                        matched_user = True
                    if exp_param.name.name == 'group_folder_name' and \
                            exp_param.string_value == group_folder_name:
                        matched_group = True
                if matched_uploader_uuid and \
                        (need_to_match_user and matched_user or
                         need_to_match_group and matched_group):
                    experiment_id = exp_pset.experiment.id
                    exp_list = Experiment.objects.filter(pk=experiment_id)
                    if exp_list[0] in Experiment.safe.all(bundle.request.user):
                        return exp_list

            return []

        return super(ExperimentAppResource, self).obj_get_list(bundle,
                                                               **kwargs)


class DataFileAppResource(tardis.tardis_portal.api.DataFileResource):
    '''Extends MyTardis's API for DataFiles to make use of the
    Uploader model's approved_storage_box in staging uploads
    (e.g. from MyData)
    '''
    temp_url = None

    class Meta(tardis.tardis_portal.api.DataFileResource.Meta):
        # This will be mapped to mydata_dataset_file by MyTardis's urls.py:
        resource_name = 'dataset_file'

    def obj_create(self, bundle, **kwargs):
        '''
        Creates a new DataFile object from the provided bundle.data dict.

        If a duplicate key error occurs, responds with HTTP Error 409: CONFLICT
        '''
        try:
            retval = super(tardis.tardis_portal.api.DataFileResource, self)\
                .obj_create(bundle, **kwargs)
        except IntegrityError as err:
            if "duplicate key" in str(err):
                raise ImmediateHttpResponse(HttpResponse(status=409))
            raise
        if 'replicas' not in bundle.data or not bundle.data['replicas']:
            # no replica specified: return upload path and create dfo for
            # new path
            datafile = bundle.obj
            try:
                if 'uploader_uuid' in bundle.data and \
                        'requester_key_fingerprint' in bundle.data:
                    uploader_uuid = bundle.data['uploader_uuid']
                    fingerprint = bundle.data['requester_key_fingerprint']
                    uploader = Uploader.objects.get(uuid=uploader_uuid)
                    uploader_registration_request = \
                        UploaderRegistrationRequest.objects.get(
                            uploader=uploader,
                            requester_key_fingerprint=fingerprint)
                    sbox = uploader_registration_request.approved_storage_box
                else:
                    ip = get_ip(bundle.request)
                    instrument_id = datafile.dataset.instrument.id
                    uploader = Uploader.objects\
                        .filter(wan_ip_address=ip,
                                instruments__id=instrument_id)\
                        .first()
                    uploader_registration_request = \
                        UploaderRegistrationRequest.objects\
                        .get(uploader=uploader)
                    sbox = uploader_registration_request.approved_storage_box
            except:
                logger.warning(traceback.format_exc())
                sbox = datafile.get_receiving_storage_box()
            if sbox is None:
                raise NotImplementedError
            dfo = DataFileObject(
                datafile=datafile,
                storage_box=sbox)
            dfo.create_set_uri()
            dfo.save()
            self.temp_url = dfo.get_full_path()
        return retval


class ReplicaAppResource(tardis.tardis_portal.api.ReplicaResource):
    '''Extends MyTardis's API for DFOs, adding in the size as measured
    by file_object.size
    '''
    class Meta(tardis.tardis_portal.api.ReplicaResource.Meta):
        # This will be mapped to mydata_replica by MyTardis's urls.py:
        resource_name = 'replica'
        authorization = ACLAuthorization()
        queryset = DataFileObject.objects.all()
        filtering = {
            'verified': ('exact',),
            'url': ('exact', 'startswith'),
        }

    def dehydrate(self, bundle):
        dfo = bundle.obj
        bundle.data['location'] = dfo.storage_box.name
        try:
            file_object_size = getattr(
                getattr(dfo, 'file_object', None), 'size', None)
        except AttributeError:
            file_object_size = None
        except IOError:
            file_object_size = None
        bundle.data['size'] = file_object_size
        return bundle
