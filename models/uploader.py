from django.db import models

from tardis.tardis_portal.models.instrument import Instrument


class Uploader(models.Model):
    '''
    Represents a PC whose user(s) have expressed interest in
    uploading to this MyTardis instance - either a PC attached to
    a data-collection instrument, or an end-user's machine.  The
    upload method (once approved) could be RSYNC over SSH to a
    staging area.  See also the UploaderRegistrationRequest model.

    To be more accurate, an uploader represents a MyTardis-upload
    program instance which is installed on a PC and is running on
    a specific network interface on that PC.  If the MyTardis-upload
    program is run on a different interface (e.g. WiFi vs Ethernet),
    then a separate uploader record should be created by the
    MyTardis-upload program, and any requests for registering the
    uploader for access to a staging area will need to be resubmitted
    for the different network device.  Using a MAC addresss to
    ensure uniqueness may seem unnecessarily complicated, compared
    to having just one uploader record per PC, but there is no
    guarantee that a MyTardis upload PC will have a fixed IP address
    or anything else which can be used to identify it uniquely.

    The Uploader model employs an unusual authorization mechanism in
    the TastyPie API.  A new uploader record can be created without
    any explicit authorization, but then it can only be retreived or
    updated if its MAC address is included in the query.  A MyTardis
    upload application can create an Uploader record immediately
    upon launch without waiting for the user to authenticate.  Then
    even if the user fails to enter valid MyTardis credentials into
    the MyTardis upload application, the MyTardis administrator will
    still have some basic information about the attempted MyTardis
    upload configuration, which can be used to help the user to
    resolve any problems they may be having with configuring
    MyTardis uploads.

    Some field values within each uploader record (e.g. IP address)
    may change after this uploader has been approved.  The MyTardis
    admin who does the approving needs to determine whether the
    Uploader's IP range needs to be added to a hosts.allow file or
    to a firewall rule, or whether an SSH key-pair is sufficient.
    '''

    name = models.CharField(max_length=64)
    contact_name = models.CharField(max_length=64)
    contact_email = models.CharField(max_length=64)

    '''
    The uploader-instrument many-to-many relationship below deserves
    some explanation.  In the first instance, the Uploader model is
    designed to represent a MyTardis-upload program running on an
    instrument computer.  In that case, each uploader record created
    from an instrument computer should be associated with exactly one
    instrument record.  However it is envisaged that MyTardis-upload
    programs could also be run from PCs which manage data from
    multiple instruments.  Conversely, one instrument could be
    associated with multiple uploaders such as multiple network
    interfaces (Ethernet and WiFi) on the same instrument PC or a
    cluster of upload PCs sharing the task of uploading data from a
    single instrument.
    '''
    instruments = \
        models.ManyToManyField("tardis_portal.Instrument", 
                               related_name="uploaders",
                               blank=True, null=True)

    user_agent_name = models.CharField(max_length=64, null=True)
    user_agent_version = models.CharField(max_length=32, null=True)
    user_agent_install_location = models.CharField(max_length=128, null=True)

    os_platform = models.CharField(max_length=64, null=True)
    os_system = models.CharField(max_length=64, null=True)
    os_release = models.CharField(max_length=32, null=True)
    os_version = models.CharField(max_length=128, null=True)
    os_username = models.CharField(max_length=64, null=True)

    machine = models.CharField(max_length=64, null=True)
    architecture = models.CharField(max_length=64, null=True)
    processor = models.CharField(max_length=64, null=True)
    memory = models.CharField(max_length=32, null=True)
    cpus = models.IntegerField(null=True)

    disk_usage = models.TextField(null=True)
    data_path = models.CharField(max_length=64, null=True)
    default_user = models.CharField(max_length=64, null=True)

    interface = models.CharField(max_length=64, default="", blank=False)
    mac_address = models.CharField(max_length=64, unique=True, blank=False)
    ipv4_address = models.CharField(max_length=16, null=True)
    ipv6_address = models.CharField(max_length=64, null=True)
    subnet_mask = models.CharField(max_length=16, null=True)

    hostname = models.CharField(max_length=64, null=True)

    # The wan_ip_address is populated in TastyPie by looking in request.META
    # It could be IPv4 or IPv6
    wan_ip_address = models.CharField(max_length=64, null=True)

    created_time = models.DateTimeField(null=True)
    updated_time = models.DateTimeField(null=True)

    class Meta:
        app_label = 'mydata'
        verbose_name_plural = 'Uploaders'

    def __unicode__(self):
        return self.name + " | " + self.interface + " | " + self.mac_address


class UploaderStagingHost(models.Model):
    '''
    Represents a file server (usually accessible via RSYNC over SSH)
    which allows "uploaders" to write to MyTardis's staging area.
    The boolean fields can remind the MyTardis administrator of the
    firewall(s) which need to be updated to authorize an uploader
    to access this staging host (via RSYNC over SSH or SCP etc.).
    '''

    host = models.CharField(default="", max_length=64)

    uses_hosts_allow = models.BooleanField()
    uses_iptables_firewall = models.BooleanField()
    uses_external_firewall = models.BooleanField()

    class Meta:
        app_label = 'mydata'
        verbose_name_plural = 'UploaderStagingHosts'

    def __unicode__(self):
        return self.host


class UploaderRegistrationRequest(models.Model):
    '''
    Represents a request to register a new instrument PC with this
    MyTardis instance and allow it to act as an "uploader".
    The upload method could be RSYNC over SSH to a staging area for example.

    We could constrain these requests to be unique per uploader record,
    but we allow an uploader to make requests using multiple key pairs,
    which could represent different user accounts on the uploader PC,
    each having its own ~/.ssh/MyData private key.
    '''

    uploader = models.ForeignKey(Uploader)

    requester_name = models.CharField(max_length=64)
    requester_email = models.CharField(max_length=64)
    requester_public_key = models.TextField()
    requester_key_fingerprint = models.CharField(max_length=64)
    request_time = models.DateTimeField(null=True, blank=True)

    approved = models.BooleanField()
    approved_staging_host = models.ForeignKey(UploaderStagingHost,
                                              null=True, blank=True,
                                              default=None)
    approved_username = models.CharField(max_length=32, null=True,
                                         blank=True, default=None)
    approver_comments = models.TextField(null=True, blank=True, default=None)
    approval_expiry = models.DateField(null=True, blank=True, default=None)
    approval_time = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        app_label = 'mydata'
        verbose_name_plural = 'UploaderRegistrationRequests'
        unique_together = ['uploader', 'requester_key_fingerprint']

    def __unicode__(self):
        return self.uploader.name + " | " + \
            self.uploader.interface + " | " + \
            self.requester_key_fingerprint + " | " + \
            self.requester_name + " | " + \
            str(self.request_time) + " | " + \
            ("Approved" if self.approved else "Not approved")