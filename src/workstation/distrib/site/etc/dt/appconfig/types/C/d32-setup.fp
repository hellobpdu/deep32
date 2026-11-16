# d32-setup.fp: First Start, Import Report and Pairing at the end of the Deep32 Status drawer (its tiles use hints
# 1 to 5).
CONTROL D32FirstStartCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32StatusSubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       30
  ICON                 D32set
  LABEL                First Start
  PUSH_ACTION          D32FirstStart
  HELP_STRING          Where the first start of this Workstation stands and what to do next.
}

CONTROL D32ImportReportCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32StatusSubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       31
  ICON                 D32acq
  LABEL                Import Report
  PUSH_ACTION          D32ImportReport
  HELP_STRING          Each step and pack of the import of disc 2.
}

CONTROL D32PairingCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32StatusSubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       32
  ICON                 D32stn
  LABEL                Pairing
  PUSH_ACTION          D32Pairing
  HELP_STRING          Pair this Workstation with a Forge, a relay or a headend, with a one-time code.
}
