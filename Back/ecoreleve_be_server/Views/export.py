from sqlalchemy import select, join, exists, func, and_, text, or_
import json
import pandas as pd
from ..Models import BaseExport, Project, Observation, Station, ModuleForms, FrontModules
from ..utils.generator import Generator
from ..renderers import CSVRenderer, PDFrenderer, GPXRenderer
from pyramid.response import Response
import io
from datetime import datetime
from ..Views import CustomView
from ..controllers.security import RootCore
from ..GenericObjets.SearchEngine import DynamicPropertiesQueryEngine, QueryEngine
from traceback import print_exc

ProcoleType = Observation.TypeClass

# class ObservationCollection(DynamicPropertiesQueryEngine):
#     pass
    
class ObservationCollection(DynamicPropertiesQueryEngine):

    def __init__(self, session, object_type=None, from_history=None):
        DynamicPropertiesQueryEngine.__init__(self, session=session, model=Observation, object_type=object_type, from_history=from_history)

    def _select_from(self):
        table_join = DynamicPropertiesQueryEngine._select_from(self)
        table_join = join(table_join, Station, Station.ID == Observation.FK_Station)

        # station_columns = [
        #     Station.Name.label('Station_Name'),
        #     Station.LAT.label('Station_Latitude'),
        #     Station.LON.label('Station_Longitude'),
        #     Station.StationDate.label('Station_Date')
        # ]
        
        station_columns = [
            Station.Name,
            Station.LAT,
            Station.LON,
            Station.StationDate
        ]

        self.selectable.extend(station_columns)
        return table_join
    def init_count_statement(self):
        '''
        override DynamicPropertiesQueryEngine.init_count_statement
        '''
        table_join = join(self.model, Station, Station.ID == Observation.FK_Station)
        query = select([func.count()]).select_from(table_join)
        return query

    def get_column_by_name(self, column_name):
        try :
            column = DynamicPropertiesQueryEngine.get_column_by_name(self, column_name)
        except:
            column = getattr(Station, column_name, None)
        return column


class CustomExportView(CustomView):

    def __init__(self, ref, parent):
        CustomView.__init__(self, ref, parent)
        try:
            self.session = self.request.registry.dbmakerExport
        except:
            ''' occures when DB export is not loaded, see development.ini :: loadDBExport '''
            pass


class ExportObservationProjectView(CustomExportView):

    item = None
    moduleFormName = 'ObservationForm'

    def __init__(self, ref, parent):
        CustomExportView.__init__(self, ref, parent)
        self.actions = {'getFields': self.getFields,
                        'getFilters': self.getFilters,
                        'count': self.count_,
                        'csv': self.export_csv,
                        'pdf': self.export_pdf,
                        'gpx': self.export_gpx,
                        'excel': self.export_excel,
                        'getFile': self.getFile
                        }
        self.type_obj = self.request.params.get('protocolType', None)
        if not self.type_obj:
            self.type_obj = self.request.params.get('typeObj', None)
        self.CollectionEngine = ObservationCollection(session=self.session, object_type=self.type_obj)

    def retrieve(self):
        if self.request.params.get('geo', None):
            return self.get_geoJSON(geoJson_properties=['taxon'])
        result = self.search()
        return [dict(row) for row in result]

    def get_geoJSON(self,geoJson_properties = None) :
        result=[]
        total=None
        countResult = self.count_()

        exceed = None
        geoJson=[]

        if countResult <= 100000 :
            exceed = False
            try :
                data=self.search()
            except :
                print_exc()
            for row in data:
                properties = {}
                if geoJson_properties != None :
                    for col in geoJson_properties :
                        properties[col] = row[col]
                geoJson.append({'type':'Feature',
                                'properties':properties,
                                'geometry':
                                    {'type':'Point',
                                     'coordinates':[row['LAT'],row['LON']]
                                    }
                                })
        else :
            exceed = True
        return {'type':'FeatureCollection', 'features': geoJson,'exceed': exceed, 'total':countResult}

    def count_(self):
        data = self.request.params.mixed()
        filters = [
            {'Column':'FK_Project','Operator':'=', 'Value':self.parent.id_},
        ]
        if 'criteria' in data:
            filters.extend(json.loads(data['criteria']))

        count = self.CollectionEngine._count(filters=filters)
        return count

    def search(self, selectable=[]):
        filters = [
            {'Column':'FK_Project','Operator':'=', 'Value':self.parent.id_},
        ]
        params = self.request.params.mixed()
        if 'criteria' in params:
            filters.extend(json.loads(params['criteria']))

        query = self.CollectionEngine.build_query(filters=filters, selectable=selectable)
        return self.session.execute(query).fetchall()

    def formatColumns(self, fileType, columns):
        queryColumns = []
        if fileType != 'gpx':
            for col in columns:
                queryColumns.append(self.table.c[col])
        else:
            splittedColumnLower = {c.name.lower().replace(
                '_', ''): c.name for c in self.table.c}
            queryColumns = [self.table.c[splittedColumnLower['lat']].label(
                'LAT'), self.table.c[splittedColumnLower['lon']].label('LON')]

            if 'stationname' in splittedColumnLower:
                queryColumns.append(self.table.c[splittedColumnLower[
                            'stationname']].label('SiteName'))
            elif 'name' in splittedColumnLower:
                queryColumns.append(self.table.c[splittedColumnLower[
                            'name']].label('SiteName'))
            elif 'sitename' in splittedColumnLower:
                queryColumns.append(self.table.c[splittedColumnLower[
                            'sitename']].label('SiteName'))
            if 'date' in splittedColumnLower:
                queryColumns.append(self.table.c[splittedColumnLower['date']].label('Date'))
        return queryColumns

    def getFile(self):
        params = self.request.params.mixed()
        criteria = json.loads(params['criteria'])
        fileType = self.request.params.get('fileType', None)
        columns = json.loads(params['columns'])

        rows = self.search(selectable=columns)
        protocol_name = self.session.query(ProcoleType).get(self.type_obj).Name
        project_name = self.session.query(Project).get(self.parent.id_).Name

        self.filename = project_name + '_'+ protocol_name + '_'
        self.request.response.content_disposition = 'attachment;filename=' + self.filename

        value = {'header': columns, 'rows': rows}

        io_export = self.actions[fileType](value)
        return io_export

    def getConf(self, moduleName=None):
        if not moduleName:
            moduleName = self.moduleFormName
        frontModule = self.session.query(FrontModules
                                  ).filter(FrontModules.Name == moduleName
                                           ).first()
        return frontModule

    def getForm(self):
        observation_fields = self.session.query(ModuleForms
                                    ).filter(
            and_(ModuleForms.Module_ID == self.getConf().ID,
                 or_(ModuleForms.TypeObj == self.type_obj, ModuleForms.TypeObj == None))).order_by(ModuleForms.FormOrder).all()

        station_fields = self.session.query(ModuleForms
                                    ).filter(ModuleForms.Module_ID == self.getConf('StationForm').ID
                                    ).filter(or_(ModuleForms.TypeObj == self.type_obj, ModuleForms.TypeObj == None)
                                    ).filter(~ModuleForms.Name.in_(['ID', 'FK_Project'])
                                    ).order_by(ModuleForms.FormOrder).all()

        all_fields = [field for field in station_fields]
        for field in observation_fields:
            all_fields.append(field)

        return all_fields

    def getFields(self):
        all_fields = self.getForm()
        column_fields = []

        for field in all_fields:
            column = {
                'field': field.Name,
                'headerName': field.Label,
                'editable': False,
                'cell': 'string',
            }
            column_fields.append(column)
        return column_fields

    def getFilters(self):
        all_fields = self.getForm()
        filters = []
        for field in all_fields:
            filters.append(self.GenerateFilter(field))

        return filters


    def GenerateFilter(self, field):
        ''' return filter field to build Filter '''
        filter_ = {
            'name': field.Name,
            'type': field.InputType,
            'label': field.Label,
            'title': field.Label,
            'editable': True,
            'validators': [],
            'options': [],
        }

        try:
            filter_['options'] = json.loads(field.Options)
        except:
            filter_['options'] = field.Options

        if field.InputType == 'Select' and field.Options is not None and 'select' in field.Options.lower():
            result = self.session.execute(text(field.Options)).fetchall()
            filter_['options'] = [
                {'label': row['label'], 'val':row['val']} for row in result]

        if field.InputType == 'Checkboxes':
            filter_['options'] = [
                {'label': 'True', 'val': 1}, {'label': 'False', 'val': 0}]

        # if (field.FilterType == 'AutocompTreeEditor'
        #         and field.Options is not None and field.Options != ''):
        #     filter_['options'] = {
        #         'startId': field.Options,
        #         'wsUrl': dbConfig['wsThesaurus']['wsUrl'],
        #         'lng': threadlocal.get_current_request().authenticated_userid['userlanguage'],
        #         'displayValueName': 'valueTranslated'}
        #     filter_['options']['startId'] = field.Options
        #     filter_['options']['ValidationRealTime'] = False
        #     filter_['options']['iconFont'] = 'reneco reneco-THE-thesaurus'

        if (field.InputType == 'TaxRefEditor'
                and field.Options is not None and field.Options != ''):
            option = json.loads(field.Options)
            filter_['options'] = filter_['options']
            filter_['options']['iconFont'] = 'reneco reneco-autocomplete'

        return filter_


    # def getFile(self):
    #     try:
    #         criteria = json.loads(self.request.params.mixed()['criteria'])
    #         fileType = criteria['fileType']
    #         # columns selection
    #         columns = criteria['columns']

    #         queryColumns = self.formatColumns(fileType, columns)

    #         query = self.generator.getFullQuery(criteria['filters'], columnsList=queryColumns)
    #         rows = self.session.execute(query).fetchall()

    #         filename = self.viewName + '.' + fileType
    #         self.request.response.content_disposition = 'attachment;filename=' + filename
    #         value = {'header': columns, 'rows': rows}

    #         io_export = self.actions[fileType](value)
    #         return io_export

    #     except:
    #         raise

    def export_csv(self, value):
        csvRender = CSVRenderer()
        csv = csvRender(value, {'request': self.request})
        return Response(csv)

    def export_pdf(self, value):
        pdfRender = PDFrenderer()
        pdf = pdfRender(value, self.viewName, self.request)
        return Response(pdf)

    def export_gpx(self, value):
        gpxRender = GPXRenderer()
        gpx = gpxRender(value, self.request)
        return Response(gpx)

    def export_excel(self, value):
        df = pd.DataFrame(data=value['rows'], columns=value['header'])

        fout = io.BytesIO()
        writer = pd.ExcelWriter(fout)
        df.to_excel(writer, sheet_name='Sheet1', index=False)
        writer.save()
        file = fout.getvalue()

        dt = datetime.now().strftime('%d-%m-%Y')
        return Response(
            file,
            content_disposition="attachment; filename="
            + self.filename + dt + ".xlsx",
            content_type='application/vnd.openxmlformats-\
            officedocument.spreadsheetml.sheet')


class ExportProtocoleTypeView(CustomExportView):

    item = ExportObservationProjectView

    def __init__(self, ref, parent):
        CustomExportView.__init__(self, ref, parent)
        self.actions = {'getFields': self.getFields,
                        'getFilters': self.getFilters
        }

    def retrieve(self):
        query = select([ProcoleType])

        table_join = join(Station, Observation, Station.ID == Observation.FK_Station)
        subQuery = select([Observation]).select_from(table_join).where(Station.FK_Project==self.parent.id_)

        ## Observation.fk_table_type_name point to FK_ProtocoleType
        subQuery = subQuery.where(ProcoleType.ID== Observation.type_id)
        query = query.where(exists(subQuery))
        result = [dict(row) for row in self.session.execute(query).fetchall()]

        return result

    def getFilters(self):
        return None

    def getFields(self):
        # table = Base.metadata.tables['Observation']
        return None

    # def get_col_observation(self):
    #     for col in table.c:
    #         field_name=col.name
    #         field_label=col.name
    #         field_type=self.table.c[col.name].type
    #         if field_type in self.dictCell:
    #             cell_type=self.dictCell[field_type]
    #             cell_type='string'
                
    #         else:
    #             cell_type='string'

    #         final.append({'field':field_name,
    #             'headerName':field_label,
    #             'cell':cell_type,
    #             'editable':False})
    #         self.cols.append({'name':field_name,'type_grid':cell_type})


    #     cols = [{'field':field_name,
    #             'headerName':field_label,
    #             'cell':cell_type,
    #             'editable':False}
    #             ]
    #     return final


class ExportProjectView(CustomExportView):

    item = None

    def __init__(self, ref, parent):
        CustomExportView.__init__(self, ref, parent)
        self.add_child('protocols', ExportProtocoleTypeView)
        self.add_child('observations', ExportObservationProjectView)
        self.id_ = ref

    def retrieve(self):
        query = select([func.count(Station.ID)]).where(Station.FK_Project== self.id_)
        result = self.session.execute(query).scalar()
        return {'nb stations': result}

    def __getitem__(self, item):
        return self.get(item)


class ExportCollectionProjectView(CustomExportView):

    item = ExportProjectView
    def retrieve(self):
        query = select([Project]).order_by(Project.Name.asc())
        result = [dict(row) for row in self.session.execute(query).fetchall()]
        return result

class ExportCoreView(CustomExportView):

    item = None

    def __init__(self, ref, parent):
        CustomExportView.__init__(self, ref, parent)
        self.add_child('projects', ExportCollectionProjectView)

    def __getitem__(self, item):
        return self.get(item)

    def retrieve(self):
        return {'next items': 'views'
                }


RootCore.listChildren.append(('export', ExportCoreView))