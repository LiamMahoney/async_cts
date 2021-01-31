import asyncio
import tempfile
import os
import configparser
import uuid
import json
from aiohttp import web, MultipartReader
from .util.db import DB

class AsyncCTS():
    """
    TODO: docstring
    """
    
    def __init__(self, searcher):
        self.searcher = searcher
        self.config = configparser.ConfigParser()
        self.config.read(os.environ.get('ASYNC_CTS_CONFIG_PATH'))

    async def getServer(self):
        """
        TODO: docstring
        """
        app = web.Application()

        app.add_routes([
            web.get('/{id}', self.retrieveArtifactResultHandler),
            web.post('/', self.scanArtifactHandler),
            web.options('/', self.queryCapabilitiesHandler)
        ])

        return app

    async def retrieveArtifactResultHandler(self, request):
        """
        TODO: docstring
        """
        id = request.match_info.get('id', 'Anonymous')
        return web.Response(text=f'Recieved retrieveArtifactResultHandler request with id {id}')

    async def scanArtifactHandler(self, request):
        """
        TODO: docstring
        """
        
        # will always be set - type / value combination of the artifact
        artifact_payload = None
        # only set for file artifacts - path to temp file and encoding
        file_payload = None

        if (request.content_type == 'application/json'):
            # non file artifact
            artifact_payload = await request.json()

        else:
            # file artifact sent w/ file
            artifact_payload, file_payload = await self.parse_multi_part_CTS_request(request)
            
        db = DB()

        # searching for the type / value combination in both the active 
        # searches and search results dbs
        past_results, active_searches = await asyncio.gather(
            db.search_for_results(artifact_type=artifact_payload.get('type'), artifact_value=artifact_payload.get('value')),
            db.search_for_active_search(artifact_type=artifact_payload.get('type'), artifact_value=artifact_payload.get('value'))
        )

        if (len(past_results) == 0 and len(active_searches) == 0):
            # no current searches or past searches with the given type / value
            # launching a new search on the type / value
            resp = asyncio.create_task(
                    self.searcher(
                        artifact_payload.get('type'), 
                        artifact_payload.get('value'),
                        file_payload=file_payload if file_payload else None
                    )
                )

            # the ID for the new search
            search_id = str(uuid.uuid4())

            # when the search is done store it in the results table & remove 
            # the entry in the active searches table
            # TODO: need to remove the active search entry, id needs to get past into callback
            resp.add_done_callback(lambda future: search_complete_handler(future, search_id, artifact_payload.get("type"), artifact_payload.get("value"), file_payload, db))

            # adding an entry to the active searches db
            await db.add_active_search(search_id, artifact_payload.get('type'), artifact_payload.get('value'))

            return web.Response(text=f"LAUNCHING SEARCH SMELL U L8R")

        elif (len(active_searches) == 1):
            #TODO: must allow multiple 'active' searches with the same type / value to use the same ID
            return web.Response(text=f"active search {active_searches[0].get('search_id')}")

        elif(len(past_results) == 1):
            # returning hit that was stored in db
            return web.json_response(json.loads(past_results[0].get("hit")))

        #TODO: this may need to be moved into the callback..
        if (file_payload):
            # deleting temp file from server
            os.unlink(file_payload.get('path'))

    async def queryCapabilitiesHandler(self, request):
        """
        TODO: docstring
        """
        supported = await self.file_uploads_supported()
        return web.Response(text=f'Recieved queryCapabilitiesHandler request')

    async def parse_multi_part_CTS_request(self, request):
        """
        Parses the multi-part CTS request into the artifact information and
        file information.

        :param aiohttp.web_request.Request request: the incomming request from
        Resilient
        :returns array index 0 describes the artifact, index 1 describes the
        file
        """

        #TODO: check if file uploads are allowed in config before parsing the file
        reader = await request.multipart()
        
        return await asyncio.gather(
            self.parse_multi_part_artifact(reader),
            self.parse_file(reader)
        )

    async def parse_multi_part_artifact(self, reader):
        """
        Parses the first part of the multi-part request which contains the
        artifact type / value combination.

        :param aiohttp.multipart.MultipartReader reader: multipart reader from
        the incomming request object
        Resilient
        :returns dict containing the artifact type / value, e.g.,
        {"type": "net.uri", "value": "https://liammahoney.dev"}
        """
        # first part of request contains typical artifact type / value
        # (value = file name in this case) json object
        artifact_part = await reader.next()
        return await artifact_part.json()

    async def parse_file(self, reader):
        """
        Creates a temporary file that contians the contents of the file sent
        by Resilient.

        :param aiohttp.multipart.MultipartReader reader: multipart reader from
        the incomming request object
        :returns dict describing the temp file created
        """
        # temporary file to write file contents to
        file_object = tempfile.NamedTemporaryFile('wb', delete=False)

        # second part of request contains the file contents
        file_part = await reader.next()

        # reading parts / chunks of file and writing to temp file
        #TODO: size should probably be limited in config
        size = 0
        while True:
            # reading raw binary data
            chunk = await file_part.read_chunk()
            if (not chunk):
                break
            size += len(chunk)
            file_object.write(chunk)
            file_object.flush()

        #TODO: is Content-Transfer-Encoding header actually needed?
        file_payload = {
            "path": file_object.name,
            "Content-Transfer-Encoding": file_part.headers.get("Content-Transfer-Encoding")
        }

        file_object.close()

        return file_payload

    async def file_uploads_supported(self):
        """
        :returns boolean True if file uploads are suppored, False if they are
        not
        """
        return self.config['cts'].getboolean('upload_files')

def search_complete_handler(future, search_id, artifact_type, artifact_value, file_payload, db):
    """
    Removes the search ID from the active searches DB and adds the results to
    the search results DB. If a file_payload is passed in then the temporary 
    file is removed from the server.

    :param future future: the results of the search (future object)
    :param string search_id: the active search ID to be removed
    :param string artifact_type: the type of the artifact
    :param string artifact_value: the value of the artifact
    :param dict file_payload: contains information about the file if one was
    sent from Resilient
    :param DB db: object that has methods to interact with the database
    """
    #TODO: need to make sure these are logged somewhere

    # schedule a task to remove the search from the active search data table 
    # and store the search results in the results table
    asyncio.create_task(db.remove_active_search(search_id))
    asyncio.create_task(db.store_search_results(search_id, artifact_type, artifact_value, json.dumps(future.result())))

    if (file_payload):
        # deleting temp file from server
        os.unlink(file_payload.get('path'))