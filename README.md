This script sync monitoring alert to pagerduty as ticket. The ticket created will be nicely integrated with actual alert(e.g. will close if resolved).
Combine with orchestration such as Jenkins/Azure DevOps/AWS Pipeline (Zabbix in my case) for cycling notif automatically.
Can be used for similiar case to other incident management tool, provided API is supported.

Refference used/related in this repo:
- [PagerDuty API](https://developer.pagerduty.com/docs/ZG9jOjExMDI5NTUw-rest-api-overview)
- [Zabbix API](https://www.zabbix.com/documentation/current/en/manual/api)
