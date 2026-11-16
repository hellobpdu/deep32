# Deep32/UX First light PREVIEW (ship lane): the Library's baked content in Deep32 Library's subpanel (position 9).
# Added beside the theme lane's deep32.fp; its controls keep their places (these take hints 20-24).

CONTROL D32ShipLibraryCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32LibrarySubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       20
  ICON                 D32lib
  LABEL                Library
  PUSH_ACTION          D32Library
  HELP_STRING          The Library baked into this preview, on one page.
}

CONTROL D32ShipEncyclopediaCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32LibrarySubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       21
  ICON                 D32srch
  LABEL                Encyclopedia
  PUSH_ACTION          D32Encyclopedia
  HELP_STRING          Wikipedia's computing articles, offline (Kiwix).
}

CONTROL D32ShipCiscoCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32LibrarySubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       22
  ICON                 D32read
  LABEL                Cisco Papers
  PUSH_ACTION          D32CiscoPapers
  HELP_STRING          Cisco Validated Designs and design guides.
}

CONTROL D32ShipRfcCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32LibrarySubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       23
  ICON                 D32rfc
  LABEL                RFCs and Drafts
  PUSH_ACTION          D32Rfcs
  HELP_STRING          Every RFC and current Internet-Draft.
}

