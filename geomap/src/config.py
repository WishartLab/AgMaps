#
# Heatmapper 
# Geomap Configuration
#
# This file contains configuration for Geomap. 


from shared import Config, ConfigHandler

config = ConfigHandler({
    
	############# CHOROPLETH CONFIG #############
	# Temporal Heatmaps. True or False
	"Temporal": Config(value=False),

	# See expression's config for explanation of dynamic inputs.
	"KeyColumn": Config(),
	"ValueColumn": Config(),
	"KeyProperty": Config(),

	# "CartoDB Positron", "OpenStreetMap"
	"MapType": Config(selected="CartoDB Positron"),

	# See shared.py for ColorMaps
	"ColorMap": Config(selected="Viridis"),

	# Any floating value between 0.0 and 1.0
	"Opacity": Config(value=0.5),

	# Any number from 3-8
	"Bins": Config(value=5),

	# Allow toggling Range of Interest
	"ROI": Config(value=False),

	# "Remove" or "Round"
	"ROI_Mode": Config(selected="Remove"),

	# Any number; the minimum/maximum bound for ROI
	"Min": Config(value=0),
	"Max": Config(value=0),

	# "Integer" "Float" "String"
	"Type": Config(selected="Integer"),

	# No value, just toggle visibility.
	"DownloadTable": Config(),
    
	# ".txt" ".csv" ".tsv" ".xlsx"
    "TableType": Config(selected=".txt"),
    
	############# COORDINATE CONFIG #############
    # See expression's config for explanation of dynamic inputs.
	"TimeColumn": Config(),
	"ValueColumnCoord": Config(),

	"RenderMode": Config(selected="Raster"),
	"RenderShape": Config(selected="Circle"),
    
	# Any floating value between 0.0 and 1.0
	"OpacityCoord": Config(value=0.7),
    
	# Any numerical value from 5-100
	"Radius": Config(value=25),
    
	# Any numerical value from 1-30
	"Blur": Config(value=15),
    
	# Allow toggling Range of Interest
	"ROICoord": Config(value=False),
	# "Remove" or "Round"
	"ROI_ModeCoord": Config(selected="Remove"),
	# Any number; the minimum/maximum bound for ROI
	"MinCoord": Config(value=0),
	"MaxCoord": Config(value=0),

})