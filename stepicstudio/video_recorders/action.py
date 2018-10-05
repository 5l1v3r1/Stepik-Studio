import logging
import os
import time
from functools import partial

from django.contrib.auth.models import User

from STEPIC_STUDIO.settings import LINUX_DIR
from stepicstudio import const
from stepicstudio.const import *
from stepicstudio.const import SUBSTEP_PROFESSOR
from stepicstudio.file_system_utils.action import *
from stepicstudio.file_system_utils.file_system_client import FileSystemClient
from stepicstudio.models import CameraStatus
from stepicstudio.operations_statuses.operation_result import InternalOperationResult
from stepicstudio.operations_statuses.statuses import ExecutionStatus
from stepicstudio.postprocessing import synchronize_videos
from stepicstudio.scheduling.task_manager import TaskManager
from stepicstudio.ssh_connections.tablet_client import TabletClient
from stepicstudio.video_recorders.camera_recorder import ServerCameraRecorder
from stepicstudio.video_recorders.tablet_recorder import TabletScreenRecorder

logger = logging.getLogger('stepic_studio.file_system_utils.action')


def to_linux_translate(win_path: str, username: str) -> str:
    linux_path = LINUX_DIR + username + '/' + '/'.join(win_path.split('/')[1:])
    logger.debug('to_linux_translate() This is linux path %s', linux_path)
    return linux_path


def start_recording(**kwargs: dict) -> InternalOperationResult:
    user_id = kwargs['user_id']
    username = User.objects.all().get(id=int(user_id)).username
    folder_path = kwargs['user_profile'].serverFilesFolder
    data = kwargs['data']
    add_file_to_test(folder_path=folder_path, data=data)
    substep_folder, a = substep_server_path(folder_path=folder_path, data=data)

    ffmpeg_status = ServerCameraRecorder().start_recording(substep_folder.replace('/', '\\'),
                                                           data['currSubStep'].name + SUBSTEP_PROFESSOR)
    if ffmpeg_status.status is not ExecutionStatus.SUCCESS:
        return ffmpeg_status

    filename = data['currSubStep'].name + const.SUBSTEP_SCREEN
    folder = to_linux_translate(substep_folder, username)
    remote_status = TabletScreenRecorder().start_recording(folder, filename)

    if remote_status.status is not ExecutionStatus.SUCCESS:
        ServerCameraRecorder().stop_recording()
        return remote_status

    db_camera = CameraStatus.objects.get(id='1')
    if not db_camera.status:
        db_camera.status = True
        db_camera.start_time = int(round(time.time() * 1000))
        db_camera.save()

    return InternalOperationResult(ExecutionStatus.SUCCESS)


def start_subtep_montage(substep_id):
    substep = SubStep.objects.get(id=substep_id)
    video_path_list = substep.os_path_all_variants
    screencast_path_list = substep.os_screencast_path_all_variants
    substep.is_locked = True
    substep.save()
    run_ffmpeg_raw_montage(video_path_list, screencast_path_list, substep_id)


def delete_substep_files(**kwargs):
    folder_path = kwargs['user_profile'].serverFilesFolder
    data = kwargs['data']
    if data['currSubStep'].is_locked:
        return False
    return delete_substep_on_disc(folder_path=folder_path, data=data)


def delete_step_files(**kwargs):
    folder_path = kwargs['user_profile'].serverFilesFolder
    data = kwargs['data']
    substeps = SubStep.objects.all().filter(from_step=data['Step'].id)
    for ss in substeps:
        print(ss.name)
        if ss.is_locked:
            return False
    return delete_step_on_disc(folder_path=folder_path, data=data)


def stop_cam_recording() -> True | False:
    camstat = CameraStatus.objects.get(id='1')
    camstat.status = False
    camstat.save()

    stop_camera_status = ServerCameraRecorder().stop_recording()
    stop_screen_status = TabletScreenRecorder().stop_recording()

    if stop_camera_status.status is not ExecutionStatus.SUCCESS or \
        stop_screen_status.status is not ExecutionStatus.SUCCESS:
        return False

    tablet_client = TabletClient()
    tablet_client.download_dir(TabletScreenRecorder().last_processed_path,
                               ServerCameraRecorder().last_processed_path)

    professor_video = os.path.join(ServerCameraRecorder().last_processed_path,
                                   ServerCameraRecorder().last_processed_file)

    screen_video = os.path.join(ServerCameraRecorder().last_processed_path,
                                TabletScreenRecorder().last_processed_file)

    convert_mkv_to_mp4(ServerCameraRecorder().last_processed_path,
                       TabletScreenRecorder().last_processed_file)

    TaskManager().run_while_idle_once_time(partial(synchronize_videos, professor_video, screen_video))

    return True


def convert_mkv_to_mp4(path: str, filename: str):
    new_filename = os.path.splitext(filename)[0] + ".mp4"  # change file extension from .mkv to .mp4
    source_file = os.path.join(path, filename)
    target_file = os.path.join(path, new_filename)
    fs_client = FileSystemClient()

    if not fs_client.validate_file(source_file):
        logger.error('Converting mkv to mp4 failed; file %s doesn\'t exist', source_file)
        return

    reencode_command = settings.FFMPEG_PATH + ' ' + \
                       settings.TABLET_REENCODE_TEMPLATE.format(source_file, target_file)

    result, _ = fs_client.execute_command(reencode_command)

    if result.status is ExecutionStatus.SUCCESS:
        logger.info('Successfully start converting mkv to mp4 (FFMPEG command: %s)', reencode_command)
    else:
        logger.error('Converting mkv to mp4 failed: %s; FFMPEG command: %s', result.message, reencode_command)


def delete_files_associated(url_args) -> True | False:
    lesson_id = int(url_args[url_args.index(COURSE_ULR_NAME) + 3])
    folder_on_server = Lesson.objects.get(id=lesson_id).os_path
    return delete_files_on_server(folder_on_server)