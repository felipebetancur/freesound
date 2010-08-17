from django.conf import settings
from piston.handler import BaseHandler
from piston.utils import rc
from search.forms import SoundSearchForm, SEARCH_SORT_OPTIONS_API
from search.views import search_prepare_sort, search_prepare_query
from sounds.models import Sound
from utils.search.solr import Solr, SolrException, SolrResponseInterpreter, SolrResponseInterpreterPaginator
import logging, os
from django.core.servers.basehttp import FileWrapper
from django.http import HttpResponse
from django.contrib.auth.models import User
from utils.search.search import add_all_sounds_to_solr
from django.contrib.sites.models import Site
from utils.pagination import paginate

logger = logging.getLogger("api")

'''
N.B.:
- #x# and %x% aliases are defined in the Freesound API v1 google doc.
- curl examples do not include authentication

TODO:
- refactor with reverse
- pack handlers
- download updates
- do something about ugly try/except statements in prepare_* functions
- check if no fields are missing
- check if tags are actually working
- throttling
'''

# UTILITY FUNCTIONS

WEB_BASE = 'http://'+Site.objects.get_current().domain
API_BASE = WEB_BASE+'/api'

def get_sound_api_url(id):
    return API_BASE+'/sounds/'+str(id)

def get_sound_web_url(username, id):
    return WEB_BASE+'/people/'+username+'/sounds/'+str(id)+'/'

def get_user_api_url(username):
    return API_BASE+'/people/'+username

def get_user_web_url(username):
    return WEB_BASE+'/people/'+username+'/'

def get_user_sounds_api_url(username):
    return API_BASE+'/people/'+username+'/sounds'

def get_user_packs_api_url(username):
    return API_BASE+'/people/'+username+'/packs'

def get_sound_links(sound):
    ref = get_sound_api_url(sound.id)
    d = {'ref': ref,
         'url': get_sound_web_url(sound.user.username, sound.id),
         'serve': ref+'/serve',
         'preview': ref+'/preview'}
    return d

def prepare_minimal_user(sound):
    return {'username': sound.user.username,
            'ref': get_user_api_url(sound.user.username),
            'url': get_user_web_url(sound.user.username),}

def prepare_single_sound(sound):
    d = {}
    for field in ["num_downloads", "channels", "duration", "samplerate", "samplerate", \
                  "id", "num_comments", "num_ratings", "filesize", "base_filename_slug", \
                  "type", "description", "original_path", "bitdepth", "bitrate",  "created", \
                  "avg_rating", "original_filename"]:
        d[field] = getattr(sound, field)
    try:
        d['license'] = sound.license.name
    except:
        pass
    try:
        d['geotag'] = [sound.geotag.lat,sound._geotag_cache.lon]
    except:
        pass
    try:
        d['user'] = prepare_minimal_user(sound)
    except:
        pass
    try:
        d['tags'] = [tag.name for tag in sound.tags.select_related("tag").all()]
    except:
        pass
    d.update(get_sound_links(sound))
    return d

def prepare_collection_sound(sound, include_user=True):
    d = {}
    for field in ["duration", "base_filename_slug", "type", "original_filename"]:
        d[field] = getattr(sound, field)
    if include_user:
        try:
            d['user'] = prepare_minimal_user(sound)
        except:
            pass
    try:
        d['tags'] = [tag.name for tag in sound.tags.select_related("tag").all()]
    except:
        pass
    d.update(get_sound_links(sound))
    return d

def prepare_single_user(user):
    d = {}
    for field in ["username", "first_name", "last_name", "date_joined"]:
        d[field] = getattr(user, field)
    d['ref'] = get_user_api_url(user.username)
    d['url'] = get_user_web_url(user.username)
    d['about'] = user.profile.about
    d['home_page'] = user.profile.home_page
    d['signature'] = user.profile.signature
    d['sounds'] = get_user_sounds_api_url(user.username)
    d['packs'] = get_user_packs_api_url(user.username)
    return d

# HANDLERS

class SoundSearchHandler(BaseHandler):
    '''
    api endpoint:   /sounds/search
    '''
    allowed_methods = ('GET',)

    '''
    input:          q, f, p, s
    output:         #paginated_search_results#
    curl:           curl http://www.freesound.org/api/search/?q=hoelahoep
    '''
    def read(self, request):
        form = SoundSearchForm(SEARCH_SORT_OPTIONS_API, request.GET)
        if not form.is_valid():
            resp = rc.BAD_REQUEST
            resp.content = form.errors
            return resp
        
        cd = form.cleaned_data

        solr = Solr(settings.SOLR_URL)    
    
        query = search_prepare_query(cd['query'],
                                     cd['filter'], 
                                     search_prepare_sort(cd['sort'], SEARCH_SORT_OPTIONS_API),
                                     cd['page'],
                                     settings.SOUNDS_PER_API_RESPONSE)
        
        try:
            results = SolrResponseInterpreter(solr.select(unicode(query)))
            paginator = SolrResponseInterpreterPaginator(results, settings.SOUNDS_PER_API_RESPONSE)
            page = paginator.page(form.cleaned_data['page'])
            
            sounds = [prepare_collection_sound(Sound.objects.select_related('user').get(id=sound_id)) \
                      for sound_id in page['object_list']]
            result = {'sounds': sounds}
            print result
            # construct previous and next urls
            if page['has_other_pages']:
                if page['has_previous']:
                    result['previous'] = self.__construct_pagination_link(cd['query'], 
                                                                          page['previous_page_number'], 
                                                                          cd['filter'], 
                                                                          cd['sort'])
                if page['has_next']:
                    result['next'] = self.__construct_pagination_link(cd['query'], 
                                                                      page['next_page_number'], 
                                                                      cd['filter'], 
                                                                      cd['sort'])
            return result
        except SolrException, e:
            error = "search error: search_query %s filter_query %s sort %s error %s" \
                        % (cd['search'], cd['filter'], cd['sort'], e)
            logger.warning(error)
            resp = rc.INTERNAL_ERROR
            resp.content = error
            return resp
    
    def __construct_pagination_link(self, q, p, f, s):
        return API_BASE+'/sounds/search?q=%s&p=%s&f=%s&s=%s' % (q,p,f,s)

class SoundHandler(BaseHandler):
    '''
    api endpoint:   /sounds/<sound_id>
    '''
    allowed_methods = ('GET',)
    
    '''
    input:          n.a.
    output:         #single_sound#
    curl:           curl http://www.freesound.org/api/sounds/2
    '''
    def read(self, request, sound_id):
        
        try:
            sound = Sound.objects.select_related('geotag', 'user', 'license', 'tags').get(id=sound_id, moderation_state="OK", processing_state="OK")
        except Sound.DoesNotExist: #@UndefinedVariable
            resp = rc.NOT_FOUND
            resp = 'There is no sound with id %s' % sound_id
            return resp
        return prepare_single_sound(sound)
        
class SoundServeHandler(BaseHandler):
    '''
    api endpoint:    /sounds/serve|preview
    '''
    allowed_methods = ('GET',)
    
    '''
    input:        n.a.
    output:       binary file
    curl:         curl http://www.freesound.org/api/sounds/2/serve
    '''
    def read(self, request, sound_id, file_or_preview):
        if not file_or_preview in ['serve', 'preview']:
            resp = rc.NOT_FOUND
            return resp
        try:
            sound = Sound.objects.get(id=sound_id, moderation_state="OK", processing_state="OK")
        except Sound.DoesNotExist: #@UndefinedVariable
            resp = rc.NOT_FOUND
            resp = 'There is no sound with id %s' % sound_id
            return resp
        sound_path = sound.paths()["sound_path"] if file_or_preview == 'serve' else sound.paths()['preview_base']
        if settings.DEBUG:
            file_path = os.path.join(settings.DATA_PATH, sound_path)
            wrapper = FileWrapper(file(file_path, "rb"))
            response = HttpResponse(wrapper, content_type='application/octet-stream')
            response['Content-Length'] = os.path.getsize(file_path)
            return response
        else:
            response = HttpResponse()
            response['Content-Type']="application/octet-stream"
            response['X-Accel-Redirect'] = os.path.join("downloads/", sound_path)
            return response

class UserHandler(BaseHandler):
    '''
    api endpoint:   /people/<username>
    '''
    allowed_methods = ('GET',)

    '''
    input:          n.a.
    output:         #single_user#
    curl:           curl http://www.freesound.org/api/people/vincent_akkermans
    '''
    def read(self, request, username):
        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            resp = rc.NOT_FOUND
            resp.content = 'This user (%s) does not exist.' % username
            return resp
        return prepare_single_user(user)
    
class UserSoundsHandler(BaseHandler):
    '''
    api endpoint:   /people/<username>/sounds
    '''
    allowed_methods = ('GET',)

    '''
    input:          p
    output:         #user_sounds#
    curl:           curl http://www.freesound.org/api/people/vincent_akkermans/sounds?p=5
    '''
    def read(self, request, username):
        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            resp = rc.NOT_FOUND
            resp.content = 'This user (%s) does not exist.' % username
            return resp
        p = request.GET.get('p', 0)    
        page = paginate(request, Sound.public.filter(user=user), settings.SOUNDS_PER_API_RESPONSE, p)['page']
        sounds = [prepare_collection_sound(sound, include_user=False) for sound in page.object_list]
        result = {'sounds': sounds}
        if page.has_other_pages():
            if page.has_previous():
                result['previous'] = self.__construct_pagination_link(username, page.previous_page_number())
            if page.has_next():
                result['next'] = self.__construct_pagination_link(username, page.next_page_number())
        return result
    
    def __construct_pagination_link(self, u, p):
        return API_BASE+'/people/%s/sounds?p=%s' % (u, p)

class UserPacksHandler(BaseHandler):
    pass

# N.B. don't add this to a production environment!

class UpdateSolrHandler(BaseHandler):
    allowed_methods = ('GET',)
    
    def read(self, request):
        add_all_sounds_to_solr()
        return rc.ALL_OK
    