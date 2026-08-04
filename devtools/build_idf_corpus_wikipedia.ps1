# Synthelion - Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# (c) 2026 Passaro Francesco Paolo - Digitalsolutions.it
#
# Offline pipeline - NOT part of the shipped package. Downloads the smallest
# available real-article Wikipedia dump per language (Wikimedia no longer
# publishes the old lightweight "-abstract.xml" dumps for most wikis as of
# this writing - confirmed by checking the itwiki dump index - so this uses
# the first "pages-articles-multistream" shard instead: for large wikis that
# means multistream1.xml-p1pN.bz2, the lowest page-id shard, which is the
# smallest split available; small wikis that aren't split at all just ship
# one multistream.xml.bz2 file), extracts plain-text articles with
# extract_wikipedia_dump.py, then builds {iso3}.idf.br with build_idf_corpus.py.
#
# Not run automatically by anything - this is a manual, one-off/occasional
# data-generation pipeline, meant to be executed "when possible" (dumps are
# hundreds of MB each) rather than as part of any install/build/test step.
#
# Usage:
#   .\devtools\build_idf_corpus_wikipedia.ps1
#   .\devtools\build_idf_corpus_wikipedia.ps1 -Languages en,it -Force
#
param(
    # Wikimedia wiki-code -> ISO 639-3. Defaults to every language shipped in
    # synthelion/worddata (56 base *.yaml.br entries as of this writing), not just
    # a curated subset.
    [string[]]$Languages = @(
        "af","ar","be","bn","bg","ca","cs","da","de","el","en","et","eu","fa","fi","fr",
        "ga","gl","he","hi","hr","hu","hy","id","is","it","ja","kn","kk","ko","la","lv",
        "lt","mr","mk","ms","nl","no","pl","pt","ro","ru","sk","sl","es","sq","sr","sv",
        "ta","te","th","tr","uk","ur","vi","zh"
    ),
    [string]$WorkDir = (Join-Path $PSScriptRoot "wikipedia_corpus"),
    [string]$PythonExe = "python",
    [int]$MinChars = 200,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Wikimedia wiki-code -> ISO 639-3, matching the base *.yaml.br files in synthelion/worddata.
$Iso3Map = @{
    af = "afr"; ar = "ara"; be = "bel"; bn = "ben"; bg = "bul"; ca = "cat"; cs = "ces"
    da = "dan"; de = "deu"; el = "ell"; en = "eng"; et = "est"; eu = "eus"; fa = "fas"
    fi = "fin"; fr = "fra"; ga = "gle"; gl = "glg"; he = "heb"; hi = "hin"; hr = "hrv"
    hu = "hun"; hy = "hye"; id = "ind"; is = "isl"; it = "ita"; ja = "jpn"; kn = "kan"
    kk = "kaz"; ko = "kor"; la = "lat"; lv = "lav"; lt = "lit"; mr = "mar"; mk = "mkd"
    ms = "msa"; nl = "nld"; no = "nor"; pl = "pol"; pt = "por"; ro = "ron"; ru = "rus"
    sk = "slk"; sl = "slv"; es = "spa"; sq = "sqi"; sr = "srp"; sv = "swe"; ta = "tam"
    te = "tel"; th = "tha"; tr = "tur"; uk = "ukr"; ur = "urd"; vi = "vie"; zh = "zho"
}

function Resolve-DumpUrl {
    param([string]$Lang)

    $indexUrl = "https://dumps.wikimedia.org/${Lang}wiki/latest/"
    $html = Invoke-WebRequest -Uri $indexUrl -UseBasicParsing -TimeoutSec 30 | Select-Object -ExpandProperty Content

    $splitMatch = [regex]::Match($html, "${Lang}wiki-latest-pages-articles-multistream1\.xml-p\d+p\d+\.bz2")
    if ($splitMatch.Success) {
        return "$indexUrl$($splitMatch.Value)"
    }
    $singleMatch = [regex]::Match($html, "${Lang}wiki-latest-pages-articles-multistream\.xml\.bz2")
    if ($singleMatch.Success) {
        return "$indexUrl$($singleMatch.Value)"
    }
    throw "Could not find a pages-articles-multistream dump for '$Lang' at $indexUrl"
}

foreach ($lang in $Languages) {
    $iso3 = $Iso3Map[$lang]
    if (-not $iso3) {
        Write-Warning "No ISO 639-3 mapping for wiki code '$lang' - skipping. Add it to `$Iso3Map if needed."
        continue
    }

    Write-Host "=== $lang ($iso3) ===" -ForegroundColor Cyan
    $langDir = Join-Path $WorkDir $lang
    New-Item -ItemType Directory -Force -Path $langDir | Out-Null

    $dumpPath = Join-Path $langDir "dump.bz2"
    $corpusPath = Join-Path $langDir "corpus.txt"
    $idfOutput = Join-Path $PSScriptRoot "..\synthelion\worddata\$iso3.idf.br"

    if ((Test-Path $idfOutput) -and -not $Force) {
        Write-Host "  $iso3.idf.br already exists - skipping (use -Force to rebuild)."
        continue
    }

    if (-not (Test-Path $dumpPath) -or $Force) {
        Write-Host "  Resolving dump URL..."
        $url = Resolve-DumpUrl -Lang $lang
        Write-Host "  Downloading $url ..."
        Invoke-WebRequest -Uri $url -OutFile $dumpPath -UseBasicParsing
    }
    else {
        Write-Host "  Dump already downloaded at $dumpPath (use -Force to re-download)."
    }

    Write-Host "  Extracting plain-text articles..."
    & $PythonExe (Join-Path $PSScriptRoot "extract_wikipedia_dump.py") `
        --dump $dumpPath --output $corpusPath --min-chars $MinChars
    if ($LASTEXITCODE -ne 0) { throw "extract_wikipedia_dump.py failed for '$lang'" }

    Write-Host "  Building $iso3.idf.br..."
    & $PythonExe (Join-Path $PSScriptRoot "build_idf_corpus.py") `
        --iso3 $iso3 --corpus-dir $langDir
    if ($LASTEXITCODE -ne 0) { throw "build_idf_corpus.py failed for '$lang'" }
}

Write-Host "Done. Generated tables are in synthelion/worddata/*.idf.br" -ForegroundColor Green
