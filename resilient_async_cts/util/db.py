import asyncio
import asyncpg
import configparser
import os
import datetime
from functools import wraps

def handle_db_connection(func):
    """
    Decorator that creates a connection to the database, executes the function
    using the decorator and then closes the connection.
    NOTE: must be called from an instance of DB, or any class that has database
    information stored in the proper variables.
    """
    @wraps(func)
    async def inner(self, *args, **kwargs):
        # making connection to database with information provided in config
        conn = await asyncpg.connect(
            user=self.config['database']['username'], 
            password=self.config['database']['password'], 
            database=self.config['database']['database'], 
            host=self.config['database']['host']
        )
        try:
            execution_results = await func(self, conn, *args, **kwargs)
        finally:
            await conn.close()
        return execution_results
    return inner

class DB():
    """
    Class that handles interacting with the data base to look for existing
    searches or results, add new searches or results and modify the data tables
    to keep their state in sync with what is needed for the application.
    """

    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config.read(os.environ.get('ASYNC_CTS_CONFIG_PATH'))

    @handle_db_connection
    async def search_for_active_search(self, conn, search_id=None, artifact_type=None, artifact_value=None):
        """
        Searches for an active search based on a search ID or an artifact type
        and value combination.
        NOTE: either artifact_type and artifact_value are required or just
        search_id.

        :param asyncpg.connection.Connection conn: connection to the database
        :param string search_id: the ID of the search
        :param string artifact_type: the type of the artifact, if supplied
        artifact_value must also be supplied
        :param string artifact_value: value of the artifact, if supplied 
        artifact_type must also be supplied
        """

        if (not search_id and (not artifact_type or not artifact_value)):
            # no parameters provided
            raise Exception("search_id or artifact_typea and artifact_value are required parameters")

        row = None

        if (search_id):
            # searching for a search_id
            row = await conn.fetch(f"SELECT * FROM {self.config['cts']['id']}_active_searches WHERE search_id = '{search_id}';")
        else:
            # searching for type / value combination
            row = await conn.fetch(f"SELECT * FROM {self.config['cts']['id']}_active_searches WHERE artifact_type = '{artifact_type}' AND artifact_value = '{artifact_value}';")

        return row

    @handle_db_connection
    async def store_search_results(self, conn, search_id, artifact_type, artifact_value, hit):
        """
        Stores the results of a search in the results db. 

        :param asyncpg.connection.Connection conn: connection to the database
        :param int search_id: the ID of the search
        :param string artifact_type: the type of the artifact
        :param string artifact_value: value of the artifact
        :returns int the search_id associated to the results
        """

        # returning search_id
        results = await conn.fetchval(f"""
            INSERT INTO {self.config['cts']['id']}_results (search_id, artifact_type, artifact_value, hit, date_found)
            VALUES ($1, $2, $3, $4, $5) RETURNING search_id;
        """, search_id, artifact_type, artifact_value, hit, datetime.datetime.now())

        return results

    @handle_db_connection
    async def search_for_results(self, conn, search_id=None, artifact_type=None, artifact_value=None):
        """
        Returns any results for the given artifact type / value combination or
        search id.
        NOTE: either artifact_type and artifact_value are required or just
        search_id.

        :param asyncpg.connection.Connection conn: connection to the database
        :param string search_id: the ID of the search
        :param string artifact_type: the type of the artifact, if supplied
        artifact_value must also be supplied
        :param string artifact_value: value of the artifact, if supplied 
        artifact_type must also be supplied
        :returns 
        """

        if (not search_id and (not artifact_type or not artifact_value)):
            # no parameters provided
            raise Exception("search_id or artifact_typea and artifact_value are required parameters")

        results = None

        if (search_id):
            # search_id supplied, retrieve results with it
            results = await conn.fetch(f"SELECT * FROM {self.config['cts']['id']}_results WHERE search_id = '{search_id}';")
        else:
            # artifact type / value supllied, retrieve results wtih it
            results = await conn.fetch(f"SELECT * FROM {self.config['cts']['id']}_results WHERE artifact_type = '{artifact_type}' AND artifact_value = '{artifact_value}';")

        return results

    @handle_db_connection
    async def remove_active_search(self, conn, search_id):
        """
        Deletes the entry from the active_searches table. Should occur when
        the search is complete.
        
        :param asyncpg.connection.Connection conn: connection to the database
        :param string search_id: the search ID to remove from the 
        active_searches table
        """

        results = await conn.execute(f"""
            DELETE FROM {self.config['cts']['id']}_active_searches
            WHERE search_id = $1
        """, search_id)

        return results
    
    @handle_db_connection
    async def add_active_search(self, conn, artifact_type, artifact_value):
        """
        Adds an entry to the 'active_searches' table. Should be called when a
        new search is started.

        :param asyncpg.connection.Connection conn: connection to the database
        :param string artifact_type: the type of the artifact that is being 
        searched
        :param string artifact_value: the value of the artifact that is being
        searched
        
        :returns int the ID of the active search - auto incremented primary
        key
        """
        search_id = await conn.fetchval(f"""
            INSERT INTO {self.config['cts']['id']}_active_searches (artifact_type, artifact_value)
            VALUES ($1, $2) RETURNING search_id;
        """, artifact_type, artifact_value)

        return search_id
    
    async def test_connection(self):
        """
        Tests the connection to CTS Hub (the database for this CTS). If the
        connection is unsuccessful the CTS should not run.

        :returns Boolean True if the connection was successful
        :raises CTSHubConnectionError if the CTS is unable to connect to CTS 
        Hub
        """
        try:
            conn = await asyncpg.connect(
                user=self.config['database']['username'], 
                password=self.config['database']['password'], 
                database=self.config['database']['database'], 
                host=self.config['database']['host']
            )
        except Exception as e:
            # exception chaining with e
            raise CTSHubConnectionError(f"failed to connect to CTS Hub running on server {self.config['database']['host']}. Please verify CTS Hub is running on the host and try again") from e

        return True
    
    @handle_db_connection
    async def cts_tables_exist(self, conn):
        """
        Checks whether tables for the CTS already exist in CTS hub by searching
        for tables that have the CTS ID specified in the CTS app.config in
        their names.

        :param asyncpg.connection.Connection conn: connection to the database
        :returns Boolean whether or not the tables exist for the CTS
        """
        tables = await conn.fetch(f"""
            SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name in ('{self.config['cts']['id']}_active_searches', '{self.config['cts']['id']}_results');
        """)
        if (len(tables) > 2):
            raise MultipleTablesForCTS(f'Mutliple tables with the CTS ID {self.config["cts"]["id"]}: {tables}')
        
        # less than two tables with the CTS ID
        return len(tables) == 2
    
    @handle_db_connection
    async def create_cts_tables(self, conn):
        """
        Creates the tables that are needed for the CTS to function. Ideally
        this should only be called the first time the CTS is ran.

        :param asyncpg.connection.Connection conn: connection to the database
        :returns None nothing is returned
        """
        await conn.execute(f"CREATE TABLE {self.config['cts']['id']}_active_searches (search_id SERIAL PRIMARY KEY, artifact_type VARCHAR, artifact_value VARCHAR);")

        await conn.execute(f"CREATE TABLE {self.config['cts']['id']}_results (search_id SERIAL PRIMARY KEY, artifact_type VARCHAR, artifact_value VARCHAR, date_found timestamp, hit JSON);")

class CTSHubConnectionError(Exception):

    def __init__(self, message=None):
        super().__init__(self, message)

class MultipleTablesForCTS(Exception):

    def __init__(self, message=None):
        super().__init__(self, message)