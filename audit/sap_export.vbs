Option Explicit

Dim fromDate, toDate, sourceShift
Dim SapGuiAuto, application, connection, session, meanSession, meanGrid
Dim meanRowIndex, ean, triples, sessionCount, waitIndex, originalClipboard, clipboardSaved
Dim shell, processEnvironment, sapConnectionName, sapClient, sapUser, sapPassword, sapLanguage, sapLogonPath
Dim sapOpenErrorNumber, sapOpenErrorDescription

If WScript.Arguments.Count < 2 Or WScript.Arguments.Count > 3 Then
    WScript.StdErr.WriteLine "Se requieren fecha inicial, fecha final y turno de origen opcional."
    WScript.Quit 1
End If

fromDate = WScript.Arguments(0)
toDate = WScript.Arguments(1)
sourceShift = "ALL"
If WScript.Arguments.Count = 3 Then sourceShift = UCase(WScript.Arguments(2))
If sourceShift <> "ALL" And sourceShift <> "A" And sourceShift <> "B" Then
    WScript.StdErr.WriteLine "El turno de origen debe ser A, B o ALL."
    WScript.Quit 1
End If
clipboardSaved = False
Set shell = CreateObject("WScript.Shell")
Set processEnvironment = shell.Environment("PROCESS")
sapConnectionName = Trim(processEnvironment("SAP_CONNECTION_NAME"))
sapClient = Trim(processEnvironment("SAP_CLIENT"))
sapUser = Trim(processEnvironment("SAP_USER"))
sapPassword = processEnvironment("SAP_PASSWORD")
sapLanguage = Trim(processEnvironment("SAP_LANGUAGE"))
sapLogonPath = Trim(processEnvironment("SAPLOGON_PATH"))
If sapLanguage = "" Then sapLanguage = "EN"

On Error Resume Next
Set SapGuiAuto = GetObject("SAPGUI")
If Err.Number <> 0 Then
    Err.Clear
    If sapLogonPath <> "" Then
        shell.Run """" & sapLogonPath & """", 0, False
    Else
        shell.Run "saplogon.exe", 0, False
    End If
    For waitIndex = 1 To 60
        WScript.Sleep 500
        Err.Clear
        Set SapGuiAuto = GetObject("SAPGUI")
        If Err.Number = 0 Then Exit For
    Next
End If
If SapGuiAuto Is Nothing Then Fail "No se pudo abrir SAP Logon. Abre SAP Logon o configura SAPLOGON_PATH en .env."

Set application = SapGuiAuto.GetScriptingEngine
If Err.Number <> 0 Then Fail "SAP GUI Scripting no está habilitado."

If application.Children.Count > 0 Then
    Set connection = application.Children(0)
Else
    If sapConnectionName = "" Then Fail "Configura SAP_CONNECTION_NAME en .env para abrir la conexión SAP automáticamente."
    Set connection = application.OpenConnection(sapConnectionName, True)
    If Err.Number <> 0 Then
        sapOpenErrorNumber = CStr(Err.Number)
        sapOpenErrorDescription = Err.Description
        Err.Clear
        If sapOpenErrorDescription = "" Then sapOpenErrorDescription = "sin detalle COM"
        Fail "SAP OpenConnection fallo. Codigo " & sapOpenErrorNumber & ": " & sapOpenErrorDescription & _
             ". Verifica que '" & sapConnectionName & "' aparezca exactamente en SAP Logon."
    End If
End If

For waitIndex = 1 To 120
    If connection.Children.Count > 0 Then Exit For
    WScript.Sleep 500
Next
If connection.Children.Count = 0 Then Fail "SAP no abrió una sesión para la conexión seleccionada."

Set session = connection.Children(0)
If Err.Number <> 0 Then Fail "No se encontró una sesión de SAP."
On Error GoTo 0

EnsureLoggedIn session
Set triples = CreateObject("Scripting.Dictionary")
session.findById("wnd[0]").maximize
If sourceShift = "A" Then
    Call RunLtapQuery(session, fromDate, toDate, "06:00:00", "17:59:59", triples)
ElseIf sourceShift = "B" Then
    Call RunLtapQuery(session, fromDate, toDate, "18:00:00", "23:59:59", triples)
    Call RunLtapQuery(session, fromDate, toDate, "00:00:00", "05:59:59", triples)
Else
    Call RunLtapQuery(session, fromDate, toDate, "", "", triples)
End If

' Reuse the second SAP session for MEAN, as in the supplied SAP GUI recording.
If connection.Children.Count < 2 Then
    sessionCount = connection.Children.Count
    session.CreateSession
    For waitIndex = 1 To 100
        WScript.Sleep 100
        If connection.Children.Count > sessionCount Then Exit For
    Next
End If

If connection.Children.Count > 1 Then
    Set meanSession = connection.Children(1)
Else
    Set meanSession = session
End If

If triples.Count > 0 Then
    originalClipboard = GetClipboardText()
    clipboardSaved = True
    ReadMean meanSession, triples
    Call PutClipboardText(originalClipboard)
    clipboardSaved = False
End If

Sub EnsureLoggedIn(activeSession)
    Dim loginWait, statusText

    If Not IsLoginScreen(activeSession) Then Exit Sub
    If sapClient = "" Or sapUser = "" Or sapPassword = "" Then
        Fail "SAP requiere inicio de sesión. Configura SAP_CLIENT, SAP_USER y SAP_PASSWORD en .env."
    End If

    On Error Resume Next
    Err.Clear
    activeSession.findById("wnd[0]/usr/ctxtRSYST-MANDT").Text = sapClient
    activeSession.findById("wnd[0]/usr/txtRSYST-BNAME").Text = sapUser
    activeSession.findById("wnd[0]/usr/pwdRSYST-BCODE").Text = sapPassword
    activeSession.findById("wnd[0]/usr/txtRSYST-LANGU").Text = sapLanguage
    If Err.Number <> 0 Then Fail "No se encontraron los campos estándar de acceso de SAP."
    activeSession.findById("wnd[0]").sendVKey 0
    If Err.Number <> 0 Then Fail "No se pudo enviar el inicio de sesión a SAP."
    On Error GoTo 0

    For loginWait = 1 To 120
        WScript.Sleep 500
        If Not IsLoginScreen(activeSession) Then Exit For
        On Error Resume Next
        statusText = CleanValue(activeSession.findById("wnd[0]/sbar").Text)
        If activeSession.findById("wnd[0]/sbar").MessageType = "E" Then
            On Error GoTo 0
            Fail "SAP rechazó el inicio de sesión: " & statusText
        End If
        On Error GoTo 0
    Next
    If IsLoginScreen(activeSession) Then Fail "SAP no completó el inicio de sesión en el tiempo esperado."
End Sub

Function IsLoginScreen(activeSession)
    Dim loginField
    IsLoginScreen = False
    On Error Resume Next
    Err.Clear
    Set loginField = activeSession.findById("wnd[0]/usr/txtRSYST-BNAME")
    If Err.Number = 0 Then IsLoginScreen = True
    Err.Clear
    On Error GoTo 0
End Function

Sub RunLtapQuery(activeSession, dateFrom, dateTo, timeFrom, timeTo, selectedTriples)
    Dim ltapGrid, rowIndex, ltapDataRows, material, gridValue, stockCategory, categoryValue, tripleKey
    Dim userName, quantity, confirmationTime, missingFields, statusText, readAttempt

    activeSession.findById("wnd[0]/tbar[0]/okcd").Text = "/nZE16"
    activeSession.findById("wnd[0]").sendVKey 0
    activeSession.findById("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").Text = "LTAP"
    activeSession.findById("wnd[0]").sendVKey 0
    activeSession.findById("wnd[0]/usr/ctxtI1-LOW").Text = "M02"
    activeSession.findById("wnd[0]/usr/txtI17-LOW").Text = "DAMAGE-BOX"
    activeSession.findById("wnd[0]/usr/ctxtI9-LOW").Text = FormatSapDate(dateFrom)
    activeSession.findById("wnd[0]/usr/ctxtI9-HIGH").Text = FormatSapDate(dateTo)
    activeSession.findById("wnd[0]").sendVKey 0
    If activeSession.findById("wnd[0]/sbar").MessageType = "E" Then
        activeSession.findById("wnd[0]/usr/ctxtI9-LOW").Text = FormatSapSlashDate(dateFrom)
        activeSession.findById("wnd[0]/usr/ctxtI9-HIGH").Text = FormatSapSlashDate(dateTo)
        activeSession.findById("wnd[0]").sendVKey 0
        If activeSession.findById("wnd[0]/sbar").MessageType = "E" Then
            Fail "SAP no aceptó la fecha en formato DD.MM.AAAA ni DD/MM/AAAA."
        End If
    End If

    If timeFrom <> "" Then
        activeSession.findById("wnd[0]/usr/ctxtI10-LOW").SetFocus
        activeSession.findById("wnd[0]/usr/ctxtI10-LOW").caretPosition = 7
        activeSession.findById("wnd[0]").sendVKey 4
        activeSession.findById("wnd[1]/tbar[0]/btn[0]").Press
        activeSession.findById("wnd[0]/usr/ctxtI10-LOW").Text = timeFrom
        activeSession.findById("wnd[0]/usr/ctxtI10-HIGH").Text = timeTo
    End If
    activeSession.findById("wnd[0]").sendVKey 8

    Set ltapGrid = Nothing
    On Error Resume Next
    Set ltapGrid = activeSession.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
    On Error GoTo 0
    If ltapGrid Is Nothing Then
        statusText = CleanValue(activeSession.findById("wnd[0]/sbar").Text)
        If IsNoDataMessage(statusText) Then Exit Sub
        Fail "SAP no mostró la tabla de resultados LTAP. Mensaje SAP: " & statusText
    End If

    ' SAP includes the yellow totals footer in RowCount; it is not an LTAP record.
    ltapDataRows = ltapGrid.RowCount - 1
    For rowIndex = 0 To ltapDataRows - 1
        EnsureGridRowVisible ltapGrid, rowIndex, "MATNR"
        For readAttempt = 1 To 4
            ReadLtapFields ltapGrid, rowIndex, material, gridValue, stockCategory, userName, quantity, confirmationTime
            missingFields = MissingLtapFields(material, gridValue, stockCategory, userName, quantity, confirmationTime)
            If missingFields = "" Then Exit For
            EnsureGridRowVisible ltapGrid, rowIndex, "MATNR"
            WScript.Sleep 100
        Next
        If missingFields <> "" Then
            Fail "LTAP fila " & CStr(rowIndex + 1) & " sin dato en: " & Left(missingFields, Len(missingFields) - 2)
        End If

        WScript.Echo "LTAP" & vbTab & material & vbTab & gridValue & vbTab & stockCategory & vbTab & userName & vbTab & quantity & vbTab & confirmationTime
        categoryValue = CategoryCode(stockCategory)
        If categoryValue = "" Then Fail "LTAP fila " & CStr(rowIndex + 1) & ": STOCK CAT vacío o inválido."
        tripleKey = material & Chr(30) & gridValue & Chr(30) & categoryValue
        If categoryValue <> "" And Not selectedTriples.Exists(tripleKey) Then
            selectedTriples.Add tripleKey, Array(material, gridValue, categoryValue)
        End If
    Next
End Sub

Sub ReadLtapFields(tableGrid, targetRow, material, gridValue, stockCategory, userName, quantity, confirmationTime)
    material = CleanValue(tableGrid.GetCellValue(targetRow, "MATNR"))
    gridValue = CleanValue(tableGrid.GetCellValue(targetRow, "J_3ASIZE"))
    stockCategory = CleanValue(tableGrid.GetCellValue(targetRow, "J_4KSCAT"))
    userName = CleanValue(tableGrid.GetCellValue(targetRow, "QNAME"))
    quantity = CleanValue(tableGrid.GetCellValue(targetRow, "NISTM"))
    confirmationTime = CleanValue(tableGrid.GetCellValue(targetRow, "QZEIT"))
End Sub

Function MissingLtapFields(material, gridValue, stockCategory, userName, quantity, confirmationTime)
    MissingLtapFields = ""
    If material = "" Then MissingLtapFields = MissingLtapFields & "MATNR, "
    If gridValue = "" Then MissingLtapFields = MissingLtapFields & "J_3ASIZE, "
    If stockCategory = "" Then MissingLtapFields = MissingLtapFields & "J_4KSCAT, "
    If userName = "" Then MissingLtapFields = MissingLtapFields & "QNAME, "
    If quantity = "" Then MissingLtapFields = MissingLtapFields & "NISTM, "
    If confirmationTime = "" Then MissingLtapFields = MissingLtapFields & "QZEIT, "
End Function

Sub ReadMean(activeSession, selectedTriples)
    Dim batchMaterials, batchGrids, batchCategories, tripleKey, tripleValues, selectionValues
    Dim resultMaterial, resultGridValue, resultCategory, missingFields, readAttempt

    Set batchMaterials = CreateObject("Scripting.Dictionary")
    Set batchGrids = CreateObject("Scripting.Dictionary")
    Set batchCategories = CreateObject("Scripting.Dictionary")
    For Each tripleKey In selectedTriples.Keys
        tripleValues = selectedTriples(tripleKey)
        If Not batchMaterials.Exists(tripleValues(0)) Then batchMaterials.Add tripleValues(0), True
        If Not batchGrids.Exists(tripleValues(1)) Then batchGrids.Add tripleValues(1), True
        If Not batchCategories.Exists(tripleValues(2)) Then batchCategories.Add tripleValues(2), True
    Next

    activeSession.findById("wnd[0]/tbar[0]/okcd").Text = "/nZE16"
    activeSession.findById("wnd[0]").sendVKey 0
    activeSession.findById("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").Text = "MEAN"
    activeSession.findById("wnd[0]").sendVKey 0
    activeSession.findById("wnd[0]/usr/txtMAX_SEL").Text = "5000"

    selectionValues = batchMaterials.Keys
    SetMultipleValues activeSession, "I1", selectionValues
    selectionValues = batchGrids.Keys
    SetMultipleValues activeSession, "I8", selectionValues
    selectionValues = batchCategories.Keys
    SetMultipleValues activeSession, "I9", selectionValues
    activeSession.findById("wnd[0]").sendVKey 8

    Set meanGrid = Nothing
    On Error Resume Next
    Set meanGrid = activeSession.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
    On Error GoTo 0
    If meanGrid Is Nothing Then Exit Sub

    ' Keep only Stock IDs before reading the result grid.
    meanGrid.SetCurrentCell -1, "EAN11"
    meanGrid.SelectColumn "EAN11"
    activeSession.findById("wnd[0]/tbar[1]/btn[29]").Press
    activeSession.findById("wnd[1]/usr/ssub%_SUBSCREEN_FREESEL:SAPLSSEL:1105/ctxt%%DYN001-LOW").Text = "*200*"
    activeSession.findById("wnd[1]").sendVKey 0

    Set meanGrid = activeSession.findById("wnd[0]/usr/cntlGRID1/shellcont/shell")
    If meanGrid.RowCount >= 5000 Then Fail "MEAN alcanzó el límite de 5000 resultados; reduce el rango de fechas."

    For meanRowIndex = 0 To meanGrid.RowCount - 1
        For readAttempt = 1 To 4
            EnsureGridRowVisible meanGrid, meanRowIndex, "MATNR"
            resultMaterial = CleanValue(meanGrid.GetCellValue(meanRowIndex, "MATNR"))
            resultGridValue = CleanValue(meanGrid.GetCellValue(meanRowIndex, "J_3AKORDX"))
            resultCategory = CleanValue(meanGrid.GetCellValue(meanRowIndex, "/AFS/EANBLOCK"))
            ean = CleanValue(meanGrid.GetCellValue(meanRowIndex, "EAN11"))
            If ean <> "" Then Exit For
            WScript.Sleep 100
        Next
        If ean = "" Then Fail "MEAN fila " & CStr(meanRowIndex + 1) & " sin dato en: EAN11."
        If Left(ean, 3) = "200" Or Left(ean, 5) = "00200" Then
            missingFields = ""
            If resultMaterial = "" Then missingFields = missingFields & "MATNR, "
            If resultGridValue = "" Then missingFields = missingFields & "J_3AKORDX, "
            If resultCategory = "" Then missingFields = missingFields & "/AFS/EANBLOCK, "
            If ean = "" Then missingFields = missingFields & "EAN11, "
            If missingFields <> "" Then
                Fail "MEAN fila " & CStr(meanRowIndex + 1) & " sin dato en: " & Left(missingFields, Len(missingFields) - 2)
            End If
            WScript.Echo "MEAN" & vbTab & resultMaterial & vbTab & resultGridValue & vbTab & resultCategory & vbTab & ean
        End If
    Next
End Sub

Function IsNoDataMessage(value)
    Dim message
    message = LCase(value)
    IsNoDataMessage = (InStr(message, "no data") > 0) Or _
                      (InStr(message, "no entries") > 0) Or _
                      (InStr(message, "no table entries") > 0) Or _
                      (InStr(message, "sin datos") > 0) Or _
                      (InStr(message, "sin entradas") > 0)
End Function

Sub EnsureGridRowVisible(tableGrid, targetRow, columnName)
    Dim firstRow, visibleRows, moved

    firstRow = tableGrid.FirstVisibleRow
    visibleRows = tableGrid.VisibleRowCount
    If visibleRows < 1 Then visibleRows = 1

    moved = False
    If targetRow < firstRow Or targetRow >= firstRow + visibleRows Then
        tableGrid.FirstVisibleRow = targetRow
        moved = True
    End If

    ' Position the current cell even when the row is already in view. SAP's
    ' virtualized grid may otherwise return empty values for an unhydrated row.
    tableGrid.SetCurrentCell targetRow, columnName
    If moved Then WScript.Sleep 100
End Sub

Sub SetMultipleValues(activeSession, fieldName, selectedValues)
    If Not PutClipboardText(Join(selectedValues, vbCrLf)) Then Fail "No se pudo preparar el portapapeles para SAP."
    activeSession.findById("wnd[0]/usr/btn%_" & fieldName & "_%_APP_%-VALU_PUSH").Press
    activeSession.findById("wnd[1]/tbar[0]/btn[24]").Press
    activeSession.findById("wnd[1]/tbar[0]/btn[8]").Press
End Sub

Function GetClipboardText()
    Dim shell, process
    Set shell = CreateObject("WScript.Shell")
    Set process = shell.Exec("powershell.exe -NoProfile -STA -Command ""Add-Type -AssemblyName System.Windows.Forms; [Console]::Out.Write([Windows.Forms.Clipboard]::GetText())""")
    Do While process.Status = 0
        WScript.Sleep 10
    Loop
    GetClipboardText = process.StdOut.ReadAll()
End Function

Function PutClipboardText(value)
    Dim shell, process
    Set shell = CreateObject("WScript.Shell")
    Set process = shell.Exec("powershell.exe -NoProfile -STA -Command ""Add-Type -AssemblyName System.Windows.Forms; $value = [Console]::In.ReadToEnd(); if ($value.Length -eq 0) { [Windows.Forms.Clipboard]::Clear() } else { [Windows.Forms.Clipboard]::SetText($value) }""")
    process.StdIn.Write value
    process.StdIn.Close
    Do While process.Status = 0
        WScript.Sleep 10
    Loop
    PutClipboardText = (process.ExitCode = 0)
End Function

Function CleanValue(value)
    CleanValue = Trim(Replace(Replace(CStr(value), vbTab, " "), vbCrLf, " "))
End Function

Function CategoryCode(value)
    Dim character, index

    CategoryCode = ""
    For index = 1 To Len(value)
        character = UCase(Mid(value, index, 1))
        If Asc(character) < 65 Or Asc(character) > 90 Then Exit For
        CategoryCode = CategoryCode & character
    Next
End Function

Function FormatSapDate(value)
    FormatSapDate = Right(value, 2) & "." & Mid(value, 5, 2) & "." & Left(value, 4)
End Function

Function FormatSapSlashDate(value)
    FormatSapSlashDate = Right(value, 2) & "/" & Mid(value, 5, 2) & "/" & Left(value, 4)
End Function

Sub Fail(message)
    If clipboardSaved Then
        On Error Resume Next
        Call PutClipboardText(originalClipboard)
        On Error GoTo 0
    End If
    WScript.StdErr.WriteLine message
    WScript.Quit 1
End Sub
