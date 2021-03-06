import logging

from django.conf import settings
from django.utils.timezone import now

from stepicstudio.file_system_utils.action import get_server_disk_info
from stepicstudio.models import UserProfile
from stepicstudio.ssh_connections.tablet_client import TabletClient
from stepicstudio.utils.utils import bytes2human

logger = logging.getLogger('stepicstudio.middleware')

POLL_FREQUENCY = 5  # every 5 request


class SetLastVisitMiddleware(object):
    def process_response(self, request, response):
        if request.is_ajax:
            return response

        try:
            if hasattr(request, 'user') and request.user.is_authenticated():
                UserProfile.objects.filter(pk=request.user.pk).update(last_visit=now())
        except Exception as e:
            logger.error('Exception handled while setting last visit info: %s', e)
            pass
        return response


class SetStorageCapacityMiddleware(object):
    def __init__(self):
        self.trigger_count = 0
        try:
            self.tablet_client = TabletClient(connect_timeout=0.5)
        except Exception as e:
            logger.error('Can\'t connect to tablet: %s', str(e))
            self.tablet_client = None

    def process_response(self, request, response):
        if request.is_ajax():
            return response

        if not self.is_triggered():
            return response

        try:
            if hasattr(request, 'user') and \
                    request.user.is_authenticated():
                self.handle_server_space_info(request)
                self.handle_tablet_space_info(request)
            else:
                request.session['server_space_info'] = 'unknown'
                request.session['tablet_space_info'] = 'unknown'
                request.session['server_space_status'] = 'unknown'
                request.session['tablet_space_status'] = 'unknown'
        except Exception as e:
            logger.error('Exception handled while setting capacity info: %s', str(e))
        return response

    def handle_server_space_info(self, request):
        try:
            user_server_path = UserProfile.objects.get(user=request.user.id).serverFilesFolder
            free_server_space, total_server_space = get_server_disk_info(user_server_path)
            request.session['server_space_info'] = bytes2human(free_server_space) + \
                                                   ' / ' + \
                                                   bytes2human(total_server_space)
            if free_server_space < settings.ERROR_CAPACITY:
                request.session['server_space_status'] = 'error'
                logger.warning('Critically low server disk space: free space: %s; total space: %s',
                               bytes2human(free_server_space),
                               bytes2human(total_server_space))
            elif free_server_space < settings.WARNING_CAPACITY:
                logger.warning('Low server disk space: free space: %s; total space: %s',
                               bytes2human(free_server_space),
                               bytes2human(total_server_space))
                request.session['server_space_status'] = 'warning'
            else:
                request.session['server_space_status'] = 'normal'
        except Exception as e:
            logger.warning('Can\'t get information about server disk capacity: %s', str(e))
            request.session['server_space_info'] = 'unknown'
            request.session['server_space_status'] = 'unknown'

    def handle_tablet_space_info(self, request):
        try:
            if self.tablet_client is None:
                raise RuntimeError('Tablet client is dummy')
            tablet_free_space, total_tablet_space = self.tablet_client.get_disk_info()
            request.session['tablet_space_info'] = bytes2human(tablet_free_space) + \
                                                   ' / ' + \
                                                   bytes2human(total_tablet_space)
            if tablet_free_space < settings.ERROR_CAPACITY:
                request.session['tablet_space_status'] = 'error'
                logger.warning('Critically low tablet disk capacity: free space: %s; total space: %s',
                               bytes2human(tablet_free_space),
                               bytes2human(total_tablet_space))
            elif tablet_free_space < settings.WARNING_CAPACITY:
                logger.warning('Low tablet disk capacity: free space: %s; total space: %s',
                               bytes2human(tablet_free_space),
                               bytes2human(total_tablet_space))
                request.session['tablet_space_status'] = 'warning'
            else:
                request.session['tablet_space_status'] = 'normal'
        except Exception as e:
            logger.exception('Can\'t get information about tablet disk capacity: %s', str(e))
            request.session['tablet_space_info'] = 'unknown'
            request.session['tablet_space_status'] = 'unknown'

    def is_triggered(self):
        self.trigger_count = (self.trigger_count % POLL_FREQUENCY) + 1
        return self.trigger_count == 1
