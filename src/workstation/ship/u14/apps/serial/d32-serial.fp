# Deep32/UX D.32.12u14: Serial Console in the Lab subpanel, after Lab Desk (position 15).
# Its own file beside the theme's deep32.fp and the ship lane's d32-ship.fp; their controls keep their places.

CONTROL D32SerialCtl
{
  TYPE                 icon
  CONTAINER_NAME       D32LabSubpanel
  CONTAINER_TYPE       SUBPANEL
  POSITION_HINTS       16
  ICON                 D32sess
  LABEL                Serial Console
  PUSH_ACTION          D32SerialConsole
  HELP_STRING          Connect to a Cisco console over a USB serial cable (cu, 9600 or 115200).
}
