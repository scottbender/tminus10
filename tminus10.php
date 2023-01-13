<?php
function getConnection(){
	global $socket;
	global $started;

        $ctx = stream_context_create( array( 'ssl' => array( 'verify_peer' => FALSE, 'allow_self_signed' => TRUE ), 'socket' => array('bindto' => '0:0') ) );

	$socket = stream_socket_client(
		'ssl://irc.oftc.net:6697' , $errno , $errstr,
		5,
		STREAM_CLIENT_ASYNC_CONNECT|STREAM_CLIENT_CONNECT , $ctx
	);

	usleep( 50000 );

	if($socket == false){
		return false;
	} else {
		say('USER TMinus10 127.0.0.1 irc.jc-mp.com :I\'m a bot');
		say('NICK TMinus10');

		$started = false;

		return true;
	}
}

function secondsToTime($seconds) {
	$units = array(
		"year"   => 365*24*3600,
		"month"  =>  31*24*3600,
		"week"   =>   7*24*3600,
		"day"    =>     24*3600,
		"hour"   =>        3600,
		"minute" =>          60,
		"second" =>           1,
	);

	// specifically handle zero
	if ( $seconds == 0 ) return "0 seconds";

	$s = "";
	$count = 0;

	foreach ( $units as $name => $divisor ) {
		if ( $quot = intval($seconds / $divisor) ) {
			if($count == 3){
				break;
			}

			$s .= "$quot $name";
			$s .= (abs($quot) > 1 ? "s" : "") . ", ";
			$seconds -= $quot * $divisor;

			$count++;
		}
	}

	return substr($s, 0, -2);
}

function strtolower_utf8($inputString) {
    $outputString    = utf8_decode($inputString);
    $outputString    = strtolower($outputString);
    $outputString    = utf8_encode($outputString);
    return $outputString;
}

function connectToServer(){
	echo "Connecting.. ";
	$result = getConnection();

	while($result == false){
		echo "Failed, retrying in 5 seconds..\n";
		sleep(5);
		echo "Connecting.. ";
		$result = getConnection();
	}

	echo "Done!\n";
}

function color($color, $text){
	return chr(3).$color.$text.chr(3);
}

function bold($text){
	return chr(2).$text.chr(2);
}

function say($data) {
	global $socket;

	fwrite($socket,$data."\r\n");
}

function param($array){
	unset($array[0]);
	unset($array[1]);
	unset($array[2]);
	unset($array[3]);

	return implode(" ", $array);
}

function getData($url){
	global $ctx;

	$cache = "/home/bots/cache/".sha1($url).".json";

	if(file_exists($cache)){
		$seconds = time() - filemtime($cache);

		if($seconds >= 10){
			unlink($cache);
		} else {
			$data = file_get_contents($cache);
		}
	}

	if(!isset($data)){
		$data = file_get_contents("http://live.mobileapp.fifa.com/api/wc/".$url, 0, $ctx);

		if(!$data) return false;
	}

	$result = json_decode($data, true);

	if(!$result || !isset($result['success']) || !$result['success']){
		return false;
	}

	file_put_contents($cache, $data);

	return $result['data'];
}

function ago($time, $short = false)
{
	$then = new DateTime($time);
	$now = new DateTime();
	$delta = $then->diff($now);

	$quantities = array(
		'day' => $delta->d,
		'hour' => $delta->h,
		'minute' => $delta->i,
		'second' => $delta->s);

	if($short){
		$quantities = array(
		'd' => $delta->d,
		'h' => $delta->h,
		'm' => $delta->i,
		's' => $delta->s);
	}

	$str = '';
	foreach($quantities as $unit => $value) {
		if($value == 0) continue;
		$str .= $value . ' ' . $unit;
		if($value != 1 && !$short) {
			$str .= 's';
		}
		$str .=  ', ';
	}

	if(time() > strtotime($time)) $str = '';

	$str = $str == '' ? 'a moment' : substr($str, 0, -2);

    if($short){
		$str = str_replace(" ", "", $str);
		$str = str_replace(",", "", $str);
    }

	return $str;
}

function suffix($number){
	$ends = array('th','st','nd','rd','th','th','th','th','th','th');
	if (($number %100) >= 11 && ($number%100) <= 13)
	   $abbreviation = $number. 'th';
	else
	   $abbreviation = $number. $ends[$number % 10];

   return $abbreviation;
}

function msg($message){
	global $channel;

	say("PRIVMSG ".$channel." :".$message);
}

function message($array){
	unset($array[0]);
	unset($array[1]);
	unset($array[2]);

	return substr(implode(" ", $array), 1);
}

function httpRequest($url){
	$cache = "/home/bots/cache/".sha1($url).".json";

	if(file_exists($cache)){
		$seconds = time() - filemtime($cache);

		if($seconds >= 10){
			unlink($cache);
		} else {
			$data = file_get_contents($cache);
		}
	}

    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_USERAGENT, "PHP");
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    $result = curl_exec($ch);
    curl_close($ch);

    return $result;
}

function apiRequest($path, $querystring){
	#$server = "lldev.thespacedevs.com";
	$server = "ll.thespacedevs.com";
	$version = "2.2.0";
	$url = "https://$server/$version/$path$querystring";

	$response = httpRequest($url);
	if(strstr($response, '<title>Server Error (500)</title>') !== FALSE){
		echo "ERROR: 500 from $url\n";
		return false;
	}

	$json = json_decode($response, true);
	if($json === null){
		echo "ERROR: could not parse json from $url\n";
		return false;
	}
	# can fail with: {"detail":"Not found."}
	# or: {"detail":"Request was throttled. Expected available in 350 seconds."}
	if(isset($data['detail'])){
		echo "ERROR: api returned error from $url\n";
		echo '       detail='.$data['detail']."\n";
		return false;
	}

	return $json;
}

function is_uuid($str){
	$regex = '/^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$/';
	if(preg_match($regex, $str) === 1) {
		return true;
	}
	return false;
}

function getNextLaunches($amount){
	$data = apiRequest('launch/upcoming/', '?hide_recent_previous=true&limit='.$amount);

	if(isset($data['results'])){
		return $data['results'];
	}

	return false;
}

function getLaunch($launchID){
	return apiRequest('launch/'.$launchID.'/', '');
}

function getSpaceX($amount){
	$data = apiRequest('launch/upcoming/', '?search=SpaceX&mode=detailed&hide_recent_previous=true&limit='.$amount);

	if(isset($data['results'])){
		return $data['results'];
	}

	return false;
}

function sendLaunchMessage($launch, $extended = false){
	$seconds = strtotime($launch['net']) - time();

	$launch_message = color(7, "#".$launch['id'].": ");
	$launch_message .= color(3, $launch['name']);

	if($seconds > 0){
		$when = secondsToTime($seconds);

		$launch_message .= color(4, " in ".$when);
	}

	if(isset($launch['vidURLs'][0])){
		$launch_message .= " - watch it at ".$launch['vidURLs'][0]['url'];
	}

	msg($launch_message);

	if($extended){
		if(isset($launch['mission'])){
			msg(color(2, 'Mission: ' ).$launch['mission']['description']);
		}

		if(trim($launch['window_start']) && trim($launch['window_end'])){
			if($launch['window_start'] == $launch['window_end']){
				msg(color(2, 'Time: ' ).$launch['window_start']);
			} else {
				msg(color(2, 'Window: ' ).$launch['window_start'].' - '.$launch['window_end']);
			}
		}

		if(isset($launch['pad'])){
			msg(color(2, 'Location: ' ).$launch['pad']['name']);
		}
	}
}

$lastping = 0;
$nextcheck = 0;
$nextupdate = 0;
$channel = '#launches';

$ctx = stream_context_create(array(
		'http' => array(
			'timeout' => 1
		)
	)
);

connectToServer();

$hype_message = "!!! ";

for($i = 2; $i <= 15; $i++){
	$hype_message .= color($i, "HYPE"). " !!! ";
}

while(1){
	if(time() - $lastping > 60){
		$lastping = time();
		say('PING :irc.jc-mp.com');
	}

	if(time() > $nextcheck && isset($nicks)){
		if(time() > $nextupdate){
			$info = apiRequest('launch/upcoming/', '?hide_recent_previous=true&limit=5');

			if(isset($info['results'])){
				$cached_launches = $info['results'];
			}

			$nextupdate = time() + 300;
		}

		foreach($cached_launches AS $launch){
			$seconds = strtotime($launch['net']) - time();
			$id = $launch['id'];
			$when = [172800, 86400, 43200, 28800, 14400, 7200, 3600, 1800, 900, 600, 300, 60];

			if(isset($launch['vidURLs'][0]) && in_array($seconds, $when)){
				if($seconds == 300){
					msg('Hyping '.trim(implode(' ', $nicks)));
					msg($hype_message);
				}

				sendLaunchMessage($launch, false);

				if($seconds == 300){
					msg($hype_message);
				}
			}

			/*
			if($seconds >= 0 && $seconds <= 10){
				if($seconds == 0){
					msg(color(3, 'Lift-off!'));
				} else {
					$color = $seconds + 3;
					if($color == 9) $color = 14;
					msg(color($color, 'T-'.$seconds));
				}
			}
			*/
		}

		$nextcheck = time();
	}

	if ($result = @stream_select($_r = array( $socket ), $_e = NULL, $_w = NULL, 0, 200000)){
		$info = stream_get_meta_data($socket);

		if($info['eof'] == '1'){
			connectToServer();
		}

		$line = explode("<br />", nl2br(fread($socket, 65000)));

		foreach($line as $data){
			if(!trim($data)) continue;

			$eData = explode(" ", $data);
			$command = strtolower(substr(@$eData[3], 1));
			$param = trim(param($eData));

			if($started == false && strstr($data,'MOTD')) {
				say('MODE TMinus10 -hH+B');
				sleep(1);
				say('JOIN '.$channel);
				say('SAJOIN TMinus10 '.$channel);

				$started = true;
			}

			if($eData[1] == 353 && $eData[4] == $channel){
				$nicks = explode(" ", trim(str_replace([":", "~", "&", "@", "%", "+", "Rico", "BobTheBuilder", "Old", "TMinus10", "Ahrotahntee"], "", $data)));

				natsort($nicks);

				unset($nicks[0]);
				unset($nicks[1]);
				unset($nicks[2]);
				unset($nicks[3]);
				unset($nicks[4]);
			}

			if($eData[1] == 'PRIVMSG' && $eData[2] == $channel && $command == "!launch"){
				$info = [];

				if(trim($param)){
					if(is_uuid($param)){
						$info = apiRequest('launch/'.$param.'/', '');
					} else {
						$info = apiRequest('launch/upcoming/', '?hide_recent_previous=true&limit=1&search='.urlencode($param));
					}
				} else {
					$info = apiRequest('launch/upcoming/', '?hide_recent_previous=true&limit=1');
				}

				$launch = null;
				if(isset($info['count']) && $info['count'] > 0){
					$launch = $info['results'][0];
				} else if(isset($info['id'])){
					$launch = $info;
				} else {
					msg('Unable to find a launch with that ID.');
				}

				if($launch !== null){
					sendLaunchMessage($launch, true);
				}
			}

			if($eData[1] == 'PRIVMSG' && $eData[2] == $channel && $command == "!launches"){
				if(!trim($param) || !is_numeric($param) || $param < 1 || $param > 10){
					$amount = 3;
				} else {
					$amount = $param;
				}

				$launches = getNextLaunches($amount);

				if(is_array($launches)){
					foreach($launches AS $launch){
						sendLaunchMessage($launch, false);
					}
				} else {
					msg('Unable to get data.');
				}
			}

			if($eData[1] == 'PRIVMSG' && $eData[2] == $channel && $command == "!spacex"){
				if(!trim($param) || !is_numeric($param) || $param < 1 || $param > 10){
					$amount = 3;
				} else {
					$amount = $param;
				}

				$launches = getSpaceX($amount);

				if(is_array($launches)){
					foreach($launches AS $launch){
						sendLaunchMessage($launch, false);
					}
				} else {
					msg('Unable to get data.');
				}
			}

			if($eData[1] == 'PRIVMSG' && $eData[2] == $channel && $command == "!hype"){
				msg($hype_message);
			}

			if($eData[0] == 'JOIN' || $eData[0] == 'QUIT' || $eData[0] == 'PART'){
				say('WHO '.$channel);
			}

			if($eData[0] == 'PING') {
				say('PONG '.$eData[1]);
				say('WHO '.$channel);
			}

			if($eData[1] == 'KICK' && $eData[3] == 'TMinus10') {
				say('JOIN '.$channel);
			}
		}
	}
}
