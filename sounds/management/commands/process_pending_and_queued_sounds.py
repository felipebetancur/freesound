#
# Freesound is (c) MUSIC TECHNOLOGY GROUP, UNIVERSITAT POMPEU FABRA
#
# Freesound is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Freesound is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     See AUTHORS file.
#

from django.core.management.base import BaseCommand
from sounds.models import Sound
from optparse import make_option


class Command(BaseCommand):
    args = ''
    help = 'Process sounds which are at pending or queued processing state'

    def handle(self, *args, **options):
        count = 0
        pending_sounds = Sound.objects.filter(processing_state="PE")
        for sound in pending_sounds:
            count += 1
            sound.process()
        queued_sounds = Sound.objects.filter(processing_state="QU")
        for sound in queued_sounds:
            count += 1
            sound.process()
        print 'Done! (%i sounds processed)' % count

